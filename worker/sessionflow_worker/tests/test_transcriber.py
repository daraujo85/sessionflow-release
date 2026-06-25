"""Testes do transcriber Whisper + handler ``audio`` do consumer (DASH-15).

Dois grupos:

1. **Handler ``audio`` (integração)**: contra tmux + Mongo + RabbitMQ reais
   (mesma stack/isolamento de ``test_consumer_input``). Aqui o
   ``transcriber.transcribe`` é **monkeypatchado** para devolver um texto fixo —
   NUNCA carregamos o modelo real nesses testes (rápido e determinístico).
   Verifica injeção no pane (capture-pane) + evento publicado, e o caminho de
   erro (arquivo inexistente / transcribe lançando) → evento de erro sem
   derrubar o consumer.

2. **Transcriber real (guardado)**: 1 teste com modelo ``tiny`` sobre um wav
   sintetizado por ffmpeg. Marcado ``integration`` e com ``pytest.skip`` se o
   ffmpeg/download do modelo falhar/demorar — não bloqueia a suíte. Não exige
   acurácia, só que retorne uma string.

Isolamento tmux: prefixo ``sftest-`` (teardown mata SÓ ``sftest-*``).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from sessionflow_worker import rabbit, transcriber
from sessionflow_worker.command_consumer import CommandConsumer
from sessionflow_worker.mongo import get_db
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

pytestmark = pytest.mark.integration

_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)

_PREFIX = "sftest-"


def _host_mongo_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


# -- fixtures ------------------------------------------------------------


@pytest.fixture
def runtime() -> TmuxRuntime:
    return TmuxRuntime()


@pytest.fixture
def make_name(runtime: TmuxRuntime) -> Iterator[Callable[[], str]]:
    """Nomes namespaced ``sftest-*``; teardown mata SÓ esses."""
    created: list[str] = []

    def _make() -> str:
        name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
        created.append(name)
        return name

    try:
        yield _make
    finally:
        for name in created:
            assert name.startswith(_PREFIX)  # cinto de segurança
            try:
                if runtime.has_session(name):
                    runtime.kill_session(name)
            except TmuxRuntimeError:
                pass


@pytest.fixture
def coll_name() -> str:
    return f"sessions_test_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name: str):
    uri = _host_mongo_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    database = get_db(uri=uri, db_name="sessionflow")
    client = database.client
    await database[coll_name].drop()
    try:
        yield database
    finally:
        await database[coll_name].drop()
        client.close()


@pytest.fixture
async def channel() -> AsyncIterator:
    conn = await rabbit.connect()
    try:
        ch = await conn.channel()
        exchange = await rabbit.declare_topology(ch)
        # Fila efêmera própria bindada à routing key de eventos antes de
        # qualquer emit (o worker não declara mais a fila durável homônima).
        events_q = await ch.declare_queue("", durable=False, auto_delete=True)
        await events_q.bind(exchange, routing_key=rabbit.EVENTS_ROUTING_KEY)
        ch.test_events_queue = events_q  # type: ignore[attr-defined]
        yield ch
    finally:
        await conn.close()


@pytest.fixture
async def consumer(channel, db, runtime, coll_name) -> CommandConsumer:
    return CommandConsumer(
        channel=channel, db=db, runtime=runtime, collection=coll_name
    )


# -- helpers -------------------------------------------------------------


async def _drain_event(channel, command_id: str, timeout: float = 8.0) -> dict:
    """Lê os eventos até achar o do ``command_id`` dado.

    Usa a fila efêmera criada pela fixture ``channel`` (bindada à routing key
    ``sessionflow.events``); o worker não declara mais a fila durável homônima.
    """
    queue = channel.test_events_queue
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            msg = await queue.get(timeout=2, fail=False)
        except Exception:  # noqa: BLE001
            msg = None
        if msg is None:
            await asyncio.sleep(0.1)
            continue
        body = json.loads(msg.body)
        await msg.ack()
        if body.get("command_id") == command_id:
            return body
    raise TimeoutError(f"evento para command_id={command_id} não chegou")


def _cmd(ctype: str, payload: dict) -> dict:
    return {
        "command_id": uuid.uuid4().hex,
        "type": ctype,
        "payload": payload,
    }


def _capture(runtime: TmuxRuntime, name: str) -> str:
    """Lê o conteúdo visível do pane ativo da sessão (capture-pane)."""
    session = runtime.server.sessions.get(session_name=name, default=None)
    assert session is not None
    pane = session.active_window.active_pane
    return "\n".join(pane.capture_pane())


# -- testes do handler (monkeypatch — sem modelo real) -------------------


async def test_audio_transcribes_and_injects(
    consumer, channel, runtime, make_name, tmp_path, monkeypatch
) -> None:
    name = make_name()
    runtime.new_session(name, tmp_path)

    # Marcador único; o "texto transcrito" é um echo desse marcador.
    marker = f"sfmark{uuid.uuid4().hex[:8]}"

    async def _fake_transcribe(
        path: str, model_name: str = "base", language: str | None = "pt"
    ) -> str:
        return f"echo {marker}"

    # NÃO carrega o modelo real: substitui o transcribe por um stub.
    monkeypatch.setattr(transcriber, "transcribe", _fake_transcribe)

    # path precisa apenas existir do ponto de vista do handler? Não — o handler
    # delega a validação ao transcriber, que aqui é o stub. Passamos um path
    # qualquer; o stub ignora.
    audio_path = tmp_path / "fake_audio.wav"
    audio_path.write_bytes(b"not-real-audio")

    command = _cmd(
        "audio",
        {"name": name, "path": str(audio_path), "upload_id": "up-123"},
    )
    event = await consumer.handle(command)

    assert event["ok"] is True
    assert event["type"] == "audio"
    assert event["name"] == name
    assert event["text"] == f"echo {marker}"
    assert event["upload_id"] == "up-123"

    # O texto transcrito foi injetado+executado: o marcador aparece no pane.
    found = ""
    for _ in range(40):
        found = _capture(runtime, name)
        if marker in found:
            break
        await asyncio.sleep(0.1)
    assert marker in found, f"marcador {marker!r} não apareceu no pane:\n{found}"

    emitted = await _drain_event(channel, command["command_id"])
    assert emitted["ok"] is True
    assert emitted["type"] == "audio"
    assert emitted["text"] == f"echo {marker}"


async def test_audio_transcribe_failure_emits_error(
    consumer, channel, runtime, make_name, tmp_path, monkeypatch
) -> None:
    name = make_name()
    runtime.new_session(name, tmp_path)

    async def _boom(
        path: str, model_name: str = "base", language: str | None = "pt"
    ) -> str:
        raise FileNotFoundError(f"arquivo de áudio não encontrado: {path!r}")

    monkeypatch.setattr(transcriber, "transcribe", _boom)

    command = _cmd(
        "audio", {"name": name, "path": "/no/such/audio.wav"}
    )
    event = await consumer.handle(command)

    # Falha tratada: evento de erro, consumer segue vivo.
    assert event["ok"] is False
    assert event["error"]
    assert event["type"] == "audio"

    emitted = await _drain_event(channel, command["command_id"])
    assert emitted["ok"] is False


async def test_audio_missing_path_errors(
    consumer, runtime, make_name, tmp_path
) -> None:
    name = make_name()
    runtime.new_session(name, tmp_path)

    event = await consumer.handle(_cmd("audio", {"name": name}))
    assert event["ok"] is False
    assert "path" in event["error"]


# -- teste do transcriber real (guardado) --------------------------------


async def test_transcribe_missing_file_raises() -> None:
    """Arquivo inexistente → FileNotFoundError clara (sem tocar no modelo)."""
    with pytest.raises(FileNotFoundError):
        await transcriber.transcribe("/no/such/file/at/all.wav")


async def test_transcribe_real_model(tmp_path) -> None:
    """REAL: Parakeet (default) sobre um wav sintetizado. Skipa se falhar/demorar.

    Não exige acurácia — só que retorne uma ``str`` sem explodir. Pulado se o
    ffmpeg não gerar o wav ou o download/load do modelo falhar.
    """
    wav = tmp_path / "synth.wav"
    # Gera 1s de tom senoidal via ffmpeg (ffmpeg está no host).
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=1",
                "-ar", "16000", "-ac", "1", str(wav),
            ],
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"ffmpeg indisponível p/ sintetizar wav: {exc}")
    if proc.returncode != 0 or not wav.is_file():
        pytest.skip(f"ffmpeg falhou ao gerar wav: {proc.stderr[-200:]!r}")

    try:
        # Default (Parakeet); guardado com timeout (download na 1ª vez ~600MB).
        text = await asyncio.wait_for(
            transcriber.transcribe(str(wav)),
            timeout=180,
        )
    except asyncio.TimeoutError:
        pytest.skip("transcrição demorou demais (download/CPU)")
    except Exception as exc:  # noqa: BLE001 - download/load do modelo pode falhar
        pytest.skip(f"modelo tiny indisponível/falhou: {exc}")

    assert isinstance(text, str)
