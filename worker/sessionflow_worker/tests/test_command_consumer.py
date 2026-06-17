"""Testes de integração do CommandConsumer (TMUX-05/06/07/09/10/11).

Contra tmux + Mongo + RabbitMQ reais da stack. Isolamento:
- Sessões tmux: prefixo ``sftest-`` (teardown mata SÓ ``sftest-*``).
- Mongo: coleção isolada ``sessions_test_<uuid>`` no DB ``sessionflow`` (o
  usuário de app só tem acesso a esse DB), dropada no teardown.
- Eventos: cada teste publica numa rota efêmera própria? Não — usamos a fila
  real ``sessionflow.events``, mas filtramos por ``command_id`` único por teste
  ao consumir, drenando até achar o evento esperado.

CRÍTICO: ``build_launch_cmd`` é monkeypatched para retornar um comando inócuo
(``true``), de modo que o send-keys ao pane NUNCA dispara uma CLI de agente
real (claude/codex/...).
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

from sessionflow_worker import command_consumer as cc_mod
from sessionflow_worker import rabbit
from sessionflow_worker.command_consumer import CommandConsumer
from sessionflow_worker.mongo import get_db
from sessionflow_worker.state import SessionState
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
        # Fila efêmera própria bindada ANTES de qualquer emit, na routing key
        # de eventos. O worker não declara mais a fila durável homônima
        # ``sessionflow.events``; cada teste captura os eventos aqui.
        events_q = await ch.declare_queue("", durable=False, auto_delete=True)
        await events_q.bind(exchange, routing_key=rabbit.EVENTS_ROUTING_KEY)
        ch.test_events_queue = events_q  # type: ignore[attr-defined]
        yield ch
    finally:
        await conn.close()


@pytest.fixture
def patch_launch(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Substitui build_launch_cmd por comando inócuo; registra chamadas.

    Garante que nenhuma CLI de agente real seja lançada no pane.
    """
    calls: list[tuple] = []

    def _fake(agent_type, model, effort) -> str:
        calls.append((agent_type, model, effort))
        return "true"

    monkeypatch.setattr(cc_mod, "build_launch_cmd", _fake)
    return calls


@pytest.fixture
async def consumer(channel, db, runtime, coll_name) -> CommandConsumer:
    return CommandConsumer(
        channel=channel, db=db, runtime=runtime, collection=coll_name
    )


# -- helpers -------------------------------------------------------------


async def _drain_event(channel, command_id: str, timeout: float = 8.0) -> dict:
    """Lê de sessionflow.events até achar o evento do ``command_id`` dado.

    Faz ack de tudo que drenar (eventos de outros testes/comandos são
    descartados de forma segura). Levanta TimeoutError se não achar.

    Consome da fila efêmera de eventos criada pela fixture ``channel`` (bindada
    à routing key ``sessionflow.events`` antes de qualquer emit); o worker não
    declara mais uma fila durável homônima.
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


# -- testes --------------------------------------------------------------


async def test_create_ok(
    consumer, channel, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    name = make_name()
    command = _cmd(
        "create",
        {
            "name": name,
            "agent_type": "claude",
            "work_dir": str(tmp_path),
            "model": "sonnet",
            "effort": "Alto",
        },
    )

    event = await consumer.handle(command)
    assert event["ok"] is True

    # Sessão tmux existe.
    assert runtime.has_session(name) is True

    # Doc running em Mongo com origin sessionflow.
    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc is not None
    assert doc["status"] == SessionState.RUNNING.value
    assert doc["origin"] == "sessionflow"
    assert doc["agent_type"] == "claude"
    assert doc["model"] == "sonnet"

    # build_launch_cmd foi chamado (e enviou "true" ao pane, não a CLI real).
    assert len(patch_launch) == 1

    # Evento ok confirmado na fila de eventos.
    emitted = await _drain_event(channel, command["command_id"])
    assert emitted["ok"] is True
    assert emitted["type"] == "create"


async def test_create_duplicate_name_errors(
    consumer, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    name = make_name()
    # Pré-cria a sessão fora do consumer (simula nome já em uso).
    runtime.new_session(name, tmp_path)

    command = _cmd(
        "create",
        {"name": name, "agent_type": "claude", "work_dir": str(tmp_path)},
    )
    event = await consumer.handle(command)

    assert event["ok"] is False
    assert "já existe" in event["error"]
    # Não criou documento no Mongo.
    assert await db[coll_name].find_one({"tmux_name": name}) is None


async def test_create_missing_workdir_errors(
    consumer, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    name = make_name()
    missing = tmp_path / "nao-existe"
    command = _cmd(
        "create",
        {"name": name, "agent_type": "claude", "work_dir": str(missing)},
    )
    event = await consumer.handle(command)

    assert event["ok"] is False
    assert event["error"]
    assert runtime.has_session(name) is False
    assert await db[coll_name].find_one({"tmux_name": name}) is None


async def test_kill_marks_stopped_preserving_doc(
    consumer, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    name = make_name()
    # Cria via consumer p/ ter doc running.
    await consumer.handle(
        _cmd(
            "create",
            {"name": name, "agent_type": "claude", "work_dir": str(tmp_path)},
        )
    )
    created_doc = await db[coll_name].find_one({"tmux_name": name})
    assert created_doc is not None
    original_id = created_doc["_id"]

    event = await consumer.handle(_cmd("kill", {"name": name}))
    assert event["ok"] is True

    assert runtime.has_session(name) is False
    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc is not None  # documento preservado (histórico)
    assert doc["_id"] == original_id
    assert doc["status"] == SessionState.STOPPED.value


async def test_rename_preserves_id(
    consumer, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    old = make_name()
    new = make_name()
    await consumer.handle(
        _cmd(
            "create",
            {"name": old, "agent_type": "claude", "work_dir": str(tmp_path)},
        )
    )
    original = await db[coll_name].find_one({"tmux_name": old})
    assert original is not None
    original_id = original["_id"]

    event = await consumer.handle(_cmd("rename", {"old": old, "new": new}))
    assert event["ok"] is True

    assert runtime.has_session(new) is True
    assert runtime.has_session(old) is False

    renamed = await db[coll_name].find_one({"tmux_name": new})
    assert renamed is not None
    assert renamed["_id"] == original_id  # _id preservado
    assert renamed["display_name"] == new
    assert await db[coll_name].find_one({"tmux_name": old}) is None


async def test_resume_nonexistent_errors(
    consumer, db, runtime, coll_name, make_name
) -> None:
    ghost = make_name()
    assert runtime.has_session(ghost) is False
    event = await consumer.handle(_cmd("resume", {"name": ghost}))
    assert event["ok"] is False
    assert "não existe" in event["error"]


async def test_resume_existing_marks_running(
    consumer, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    name = make_name()
    await consumer.handle(
        _cmd(
            "create",
            {"name": name, "agent_type": "claude", "work_dir": str(tmp_path)},
        )
    )
    # Força um status diferente p/ ver o resume reconciliar p/ running.
    await db[coll_name].update_one(
        {"tmux_name": name},
        {"$set": {"status": SessionState.DETACHED.value}},
    )

    event = await consumer.handle(_cmd("resume", {"name": name}))
    assert event["ok"] is True
    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc["status"] == SessionState.RUNNING.value


async def test_dedupe_by_command_id(
    consumer, db, runtime, coll_name, make_name, patch_launch, tmp_path
) -> None:
    """Reprocessar o mesmo command_id é no-op idempotente."""
    name = make_name()
    command = _cmd(
        "create",
        {"name": name, "agent_type": "claude", "work_dir": str(tmp_path)},
    )
    first = await consumer.handle(command)
    assert first["ok"] is True
    assert len(patch_launch) == 1

    # Mesmo command_id de novo: não recria/não relança.
    second = await consumer.handle(command)
    assert second["ok"] is True
    assert second.get("deduplicated") is True
    assert len(patch_launch) == 1  # build_launch_cmd NÃO chamado de novo
