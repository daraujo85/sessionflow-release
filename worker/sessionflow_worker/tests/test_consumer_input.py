"""Testes de integração do handler ``input`` do CommandConsumer (DASH-13).

Contra tmux + Mongo + RabbitMQ reais da stack. Isolamento:
- Sessões tmux: prefixo ``sftest-`` (teardown mata SÓ ``sftest-*``).
- Mongo: coleção isolada ``sessions_test_<uuid>`` no DB ``sessionflow`` (o
  usuário de app só tem acesso a esse DB), dropada no teardown.
- Eventos: fila real ``sessionflow.events``, filtrando por ``command_id`` único.

O handler ``input`` injeta texto no pane via send-keys (enter=True). Os testes
verificam que o texto aparece no pane (via ``capture_pane``) e que sessão
inexistente vira evento de erro sem derrubar o consumer.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from sessionflow_worker import rabbit
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
        exchange = await rabbit.declare_topology(ch, "test-host")
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
        channel=channel, db=db, host_id="test-host", runtime=runtime, collection=coll_name
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


# -- testes --------------------------------------------------------------


async def test_input_sends_text_to_pane(
    consumer, channel, runtime, make_name, tmp_path
) -> None:
    name = make_name()
    # Cria a sessão direto no runtime (sem lançar agente; shell puro).
    runtime.new_session(name, tmp_path)

    # Marcador único impresso pelo shell ao receber o input.
    marker = f"sfmark{uuid.uuid4().hex[:8]}"
    command = _cmd("input", {"name": name, "text": f"echo {marker}"})

    event = await consumer.handle(command)
    assert event["ok"] is True
    assert event["type"] == "input"
    assert event["name"] == name

    # O texto deve ter sido enviado+executado: o marcador aparece no pane.
    found = ""
    for _ in range(40):
        found = _capture(runtime, name)
        if marker in found:
            break
        await asyncio.sleep(0.1)
    assert marker in found, f"marcador {marker!r} não apareceu no pane:\n{found}"

    # Evento ok confirmado na fila de eventos.
    emitted = await _drain_event(channel, command["command_id"])
    assert emitted["ok"] is True
    assert emitted["type"] == "input"


async def test_input_nonexistent_session_errors(
    consumer, channel, runtime, make_name
) -> None:
    ghost = make_name()
    assert runtime.has_session(ghost) is False

    command = _cmd("input", {"name": ghost, "text": "echo nope"})
    event = await consumer.handle(command)

    # Falha tratada: evento de erro, consumer segue vivo.
    assert event["ok"] is False
    assert event["error"]
    assert event["type"] == "input"

    emitted = await _drain_event(channel, command["command_id"])
    assert emitted["ok"] is False


async def test_input_missing_text_errors(consumer, runtime, make_name, tmp_path) -> None:
    name = make_name()
    runtime.new_session(name, tmp_path)

    event = await consumer.handle(_cmd("input", {"name": name}))
    assert event["ok"] is False
    assert "text" in event["error"]
