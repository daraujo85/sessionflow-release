"""Testes de captura de output (DASH-02).

Inclui testes PUROS (sem marker) de ``classify_line``/``detect_waiting`` e
testes de INTEGRAÇÃO (marker ``integration``) que exercitam tmux + Mongo +
Rabbit reais.

Isolamento / segurança:
- Sessões tmux: SEMPRE prefixo ``sftest-`` (teardown mata SÓ ``sftest-*``,
  com assert de prefixo). Sessões reais (planner/portal/pratinha) nunca tocadas.
- Mongo: coleção isolada ``session_output_test_<uuid>`` no DB ``sessionflow``
  (único DB acessível ao usuário de app), dropada no teardown.
- Rabbit: fila EFÊMERA própria (``auto_delete``, nome único) bind ao exchange
  ``sessionflow`` na rota ``output`` — não depende de fila durável compartilhada.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from dotenv import load_dotenv

from sessionflow_worker import rabbit
from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.mongo import get_db
from sessionflow_worker.output_capture import (
    LINE_ASK,
    LINE_CMD,
    LINE_OUT,
    LINE_TOOL,
    OUTPUT_ROUTING_KEY,
    OutputCapture,
    classify_line,
    detect_waiting,
    strip_ansi,
)
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

# -- testes puros (sem marker) ----------------------------------------------


def test_classify_line_cmd() -> None:
    assert classify_line("$ ls -la") == LINE_CMD
    assert classify_line("› npm run build") == LINE_CMD
    assert classify_line("# comment-prompt") == LINE_CMD


def test_classify_line_ask_and_tool_and_out() -> None:
    assert classify_line("Deseja aplicar a mudança? (s/n)") == LINE_ASK
    assert classify_line("Should I proceed?") == LINE_ASK
    assert classify_line("⎿ Read file.py") == LINE_TOOL
    assert classify_line("hello world output") == LINE_OUT
    assert classify_line("") == LINE_OUT
    assert classify_line("   ") == LINE_OUT


def test_detect_waiting_true_cases() -> None:
    assert detect_waiting("Aplico a correção? (s/n)", AgentType.CLAUDE) is True
    assert detect_waiting("Do you want to continue?", AgentType.CODEX) is True
    assert detect_waiting("Confirma o deploy (y/n)", AgentType.GEMINI) is True


def test_detect_waiting_false_cases() -> None:
    assert detect_waiting("rodando testes...", AgentType.CLAUDE) is False
    assert detect_waiting("", AgentType.CLAUDE) is False
    assert detect_waiting("Tudo certo.", AgentType.OPENCODE) is False


def test_strip_ansi() -> None:
    # Cores (CSI SGR).
    assert strip_ansi("\x1b[31moi\x1b[0m") == "oi"
    # Bracketed-paste (CSI ?2004l) no meio do texto.
    assert strip_ansi("x\x1b[?2004lx") == "xx"
    assert strip_ansi("\x1b[?2004hcmd") == "cmd"
    # Movimento de cursor + clear.
    assert strip_ansi("\x1b[2K\x1b[1Gprompt") == "prompt"
    # OSC (título de janela, terminado por BEL).
    assert strip_ansi("\x1b]0;title\x07texto") == "texto"
    # Texto puro inalterado.
    assert strip_ansi("sem escapes") == "sem escapes"
    # Backspace (0x08) é resolvido apagando o char anterior: corrige o
    # "eecho" -> "echo" que aparecia no output capturado.
    assert strip_ansi("e\becho") == "echo"
    # Vários backspaces seguidos apagam vários chars.
    assert strip_ansi("abcd\b\bxy") == "abxy"
    # Backspace no início (sem char anterior) é só descartado.
    assert strip_ansi("\bok") == "ok"
    # Carriage return é removido (linhas já vêm separadas).
    assert strip_ansi("linha\r") == "linha"
    # Outros controles C0 não imprimíveis somem; \t e \n são preservados.
    assert strip_ansi("a\x00\x07b") == "ab"
    assert strip_ansi("col1\tcol2") == "col1\tcol2"


# -- integração --------------------------------------------------------------

_PREFIX = "sftest-"


def _host_mongo_uri() -> str | None:
    load_dotenv(override=False)
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
def runtime() -> TmuxRuntime:
    return TmuxRuntime()


@pytest.fixture
def session_name(runtime: TmuxRuntime) -> Iterator[str]:
    name = f"{_PREFIX}out-{uuid.uuid4().hex[:8]}"
    try:
        yield name
    finally:
        assert name.startswith(_PREFIX)  # cinto de segurança
        try:
            if runtime.has_session(name):
                runtime.kill_session(name)
        except TmuxRuntimeError:
            pass


@pytest.fixture
def coll_name() -> str:
    return f"session_output_test_{uuid.uuid4().hex}"


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
async def ephemeral_queue() -> AsyncIterator[tuple]:
    """Canal + fila efêmera (auto_delete) bind à rota ``output``.

    Não usa a fila durável compartilhada: declara uma fila exclusiva de nome
    único, bind ao exchange ``sessionflow`` na routing key ``output``.
    """
    try:
        conn = await rabbit.connect()
    except Exception:  # noqa: BLE001
        pytest.skip("RabbitMQ não disponível.")
    try:
        ch = await conn.channel()
        exchange = await rabbit.declare_topology(ch, "test-host")
        queue = await ch.declare_queue(
            f"sftest.output.{uuid.uuid4().hex}", auto_delete=True, exclusive=True
        )
        await queue.bind(exchange, routing_key=OUTPUT_ROUTING_KEY)
        yield ch, queue
    finally:
        await conn.close()


@pytest.mark.integration
async def test_poll_persists_and_returns(
    runtime: TmuxRuntime, db, coll_name: str, session_name: str
) -> None:
    runtime.new_session(session_name, os.getcwd())

    cap = OutputCapture(runtime, db, collection=coll_name, max_lines=2000)
    cap.start_capture(session_name)

    # Faz o pane imprimir duas linhas determinísticas.
    session = runtime.server.sessions.get(session_name=session_name)
    session.active_window.active_pane.send_keys(
        "printf 'linha1\\nlinha2\\n'", enter=True
    )

    lines = await _poll_until(cap, session_name, minimum=2)

    texts = [ln.text for ln in lines]
    assert "linha1" in texts
    assert "linha2" in texts

    # Persistência: docs na coleção isolada com seq incremental.
    coll = db[coll_name]
    docs = [d async for d in coll.find({"tmux_name": session_name}).sort("seq", 1)]
    persisted = [d["text"] for d in docs]
    assert "linha1" in persisted
    assert "linha2" in persisted
    seqs = [d["seq"] for d in docs]
    assert seqs == sorted(seqs)
    assert seqs[0] == 0
    for d in docs:
        assert d["session_id"] == session_name
        assert "line_type" in d
        assert "at" in d


@pytest.mark.integration
async def test_poll_publishes_output_event(
    runtime: TmuxRuntime, db, coll_name: str, session_name: str, ephemeral_queue
) -> None:
    channel, queue = ephemeral_queue
    runtime.new_session(session_name, os.getcwd())

    cap = OutputCapture(runtime, db, channel=channel, collection=coll_name)
    cap.start_capture(session_name)

    session = runtime.server.sessions.get(session_name=session_name)
    session.active_window.active_pane.send_keys("printf 'evt1\\n'", enter=True)

    await _poll_until(cap, session_name, minimum=1)

    # Lê da fila efêmera um evento ``output``.
    got_texts: list[str] = []
    deadline = asyncio.get_event_loop().time() + 10
    while asyncio.get_event_loop().time() < deadline:
        msg = await queue.get(timeout=2, fail=False)
        if msg is None:
            await asyncio.sleep(0.1)
            continue
        body = json.loads(msg.body)
        await msg.ack()
        assert body["event"] == OUTPUT_ROUTING_KEY
        assert body["tmux_name"] == session_name
        got_texts.append(body["text"])
        if "evt1" in got_texts:
            break
    assert "evt1" in got_texts


@pytest.mark.integration
async def test_ring_buffer_caps_lines(
    runtime: TmuxRuntime, db, coll_name: str, session_name: str
) -> None:
    runtime.new_session(session_name, os.getcwd())

    cap = OutputCapture(runtime, db, collection=coll_name, max_lines=5)
    cap.start_capture(session_name)

    session = runtime.server.sessions.get(session_name=session_name)
    session.active_window.active_pane.send_keys(
        "for i in $(seq 1 20); do echo line$i; done", enter=True
    )

    await _poll_until(cap, session_name, minimum=20)

    coll = db[coll_name]
    count = await coll.count_documents({"tmux_name": session_name})
    assert count <= 5

    # As linhas restantes devem ser as de seq mais alto (mais recentes).
    docs = [d async for d in coll.find({"tmux_name": session_name}).sort("seq", 1)]
    seqs = [d["seq"] for d in docs]
    assert seqs == sorted(seqs)


@pytest.mark.integration
async def test_start_capture_snapshots_existing_content(
    runtime: TmuxRuntime, db, coll_name: str, session_name: str
) -> None:
    """Sessão que JÁ tem conteúdo no pane: snapshot inicial deve persistir."""
    runtime.new_session(session_name, os.getcwd())

    # Faz o pane imprimir algo ANTES de iniciar a captura.
    session = runtime.server.sessions.get(session_name=session_name)
    session.active_window.active_pane.send_keys("echo PRESET", enter=True)

    # Espera o output aparecer na tela do pane (capture-pane reflete a tela).
    await asyncio.sleep(1.0)

    cap = OutputCapture(runtime, db, collection=coll_name, max_lines=2000)
    cap.start_capture(session_name)

    # O snapshot é drenado no primeiro poll, sem precisar de output novo.
    lines = await _poll_until(cap, session_name, minimum=1)
    texts = [ln.text for ln in lines]
    assert any("PRESET" in t for t in texts)

    coll = db[coll_name]
    docs = [d async for d in coll.find({"tmux_name": session_name}).sort("seq", 1)]
    persisted = [d["text"] for d in docs]
    assert any("PRESET" in t for t in persisted)


def test_start_capture_idempotent(monkeypatch) -> None:
    """Re-chamar start_capture para a mesma sessão não re-snapshot/reinicia."""
    import sessionflow_worker.output_capture as oc

    calls: list[list[str]] = []

    def fake_run(cmd, *a, **k):  # noqa: ANN001, ANN002, ANN003
        calls.append(cmd)

        class _P:
            stdout = b""

        return _P()

    monkeypatch.setattr(oc.subprocess, "run", fake_run)

    cap = OutputCapture.__new__(OutputCapture)
    cap._runtime = type("R", (), {"server": type("S", (), {"socket_name": None})()})()
    cap._pipe_files = {}
    cap._offsets = {}
    cap._pending_snapshot = {}

    p1 = cap.start_capture("sftest-x")
    n_after_first = len(calls)
    p2 = cap.start_capture("sftest-x")

    assert p1 == p2
    # Segunda chamada não deve emitir novos comandos tmux.
    assert len(calls) == n_after_first


@pytest.mark.integration
async def test_snapshot_screen_upserts_visible_text(
    runtime: TmuxRuntime, db, session_name: str
) -> None:
    """Espelho da tela ao vivo: capture_screen + upsert no session_screen."""
    screen_coll = f"session_screen_test_{uuid.uuid4().hex}"
    await db[screen_coll].drop()
    try:
        runtime.new_session(session_name, os.getcwd())

        cap = OutputCapture(runtime, db)

        # Escreve algo determinístico via send-keys e espera aparecer na tela.
        session = runtime.server.sessions.get(session_name=session_name)
        session.active_window.active_pane.send_keys("echo MIRROR_LINE", enter=True)

        # capture-pane reflete só a tela visível; faz polling até aparecer.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 15.0
        text = ""
        while loop.time() < deadline:
            text = await cap.snapshot_screen(session_name, collection=screen_coll)
            if "MIRROR_LINE" in text:
                break
            await asyncio.sleep(0.3)
        assert "MIRROR_LINE" in text

        # Upsert: exatamente 1 doc por sessão, com o texto atual.
        coll = db[screen_coll]
        count = await coll.count_documents({"tmux_name": session_name})
        assert count == 1
        doc = await coll.find_one({"tmux_name": session_name})
        assert "MIRROR_LINE" in doc["text"]
        assert "at" in doc

        # Re-chamar substitui (não acumula) — segue 1 doc.
        await cap.snapshot_screen(session_name, collection=screen_coll)
        assert await coll.count_documents({"tmux_name": session_name}) == 1
    finally:
        await db[screen_coll].drop()


def test_capture_screen_missing_session_returns_empty() -> None:
    """Sessão inexistente -> capture_screen retorna "" (sem levantar)."""
    runtime = TmuxRuntime()
    cap = OutputCapture(runtime, db=None)  # type: ignore[arg-type]
    assert cap.capture_screen(f"{_PREFIX}does-not-exist-{uuid.uuid4().hex}") == ""


# -- helpers ----------------------------------------------------------------


async def _poll_until(cap: OutputCapture, name: str, minimum: int, timeout: float = 15.0):
    """Faz poll repetido acumulando linhas até atingir ``minimum`` (ou timeout)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    collected: list = []
    while loop.time() < deadline:
        new = await cap.poll_new_lines(name)
        collected.extend(new)
        if len(collected) >= minimum:
            return collected
        await asyncio.sleep(0.3)
    return collected
