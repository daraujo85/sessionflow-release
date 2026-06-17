"""Testes de integração da emissão de eventos (DASH-03).

Marker: ``integration``. Isolamento (mesmas lições do resto da suíte):
    - Coleção Mongo dedicada ``events_test_<uuid>`` no DB ``sessionflow``
      (único DB autorizado p/ o usuário de app), dropada no teardown.
    - RabbitMQ: exchange + fila EFÊMERAS próprias (nome único, ``auto_delete``),
      NUNCA a fila durável compartilhada ``sessionflow.events`` (outra task roda
      em paralelo nela).
    - tmux: sessões ``sftest-*``; teardown mata APENAS esse prefixo.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import aio_pika
import pytest
from dotenv import load_dotenv

from sessionflow_worker import rabbit
from sessionflow_worker.discovery import Discovery
from sessionflow_worker.events import emit_event
from sessionflow_worker.mongo import get_db
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

# `.env` da raiz: worker/sessionflow_worker/tests/test_events.py -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)

pytestmark = pytest.mark.integration

_PREFIX = "sftest-"


def _host_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
def coll_name() -> str:
    return f"events_test_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name: str) -> AsyncIterator:
    uri = _host_uri()
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
def runtime() -> TmuxRuntime:
    return TmuxRuntime()


@pytest.fixture
def make_name(runtime: TmuxRuntime) -> Iterator[Callable[[], str]]:
    """Gera nomes ``sftest-*`` e mata TODOS no teardown (apenas ``sftest-*``)."""
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


async def test_emit_event_persists_in_mongo(db, coll_name: str) -> None:
    """``emit_event`` grava o doc com seq incremental e campos esperados."""
    sid = f"sess-{uuid.uuid4().hex[:8]}"

    doc1 = await emit_event(
        db,
        type="created",
        kind="info",
        session_id=sid,
        title="Sessão criada",
        desc="primeira",
        collection=coll_name,
    )
    doc2 = await emit_event(
        db,
        type="stopped",
        kind="warning",
        session_id=sid,
        title="Sessão parada",
        desc="segunda",
        collection=coll_name,
    )

    # seq incremental por coleção, começando em 1.
    assert doc1["seq"] == 1
    assert doc2["seq"] == 2

    persisted = await db[coll_name].find_one({"session_id": sid, "type": "created"})
    assert persisted is not None
    assert persisted["kind"] == "info"
    assert persisted["title"] == "Sessão criada"
    assert persisted["desc"] == "primeira"
    assert persisted["seq"] == 1
    assert "at" in persisted

    total = await db[coll_name].count_documents({"session_id": sid})
    assert total == 2


async def test_emit_event_rejects_invalid_type_and_kind(db, coll_name: str) -> None:
    """Validação de ``type`` e ``kind``; nada é persistido em caso de erro."""
    with pytest.raises(ValueError):
        await emit_event(
            db,
            type="bogus",
            kind="info",
            session_id="x",
            title="t",
            desc="d",
            collection=coll_name,
        )
    with pytest.raises(ValueError):
        await emit_event(
            db,
            type="created",
            kind="bogus",
            session_id="x",
            title="t",
            desc="d",
            collection=coll_name,
        )
    assert await db[coll_name].count_documents({}) == 0


async def test_emit_event_publishes_to_ephemeral_queue(db, coll_name: str) -> None:
    """Com channel, ``emit_event`` publica numa fila/exchange EFÊMERAS próprias."""
    try:
        conn = await rabbit.connect()
    except Exception as exc:  # noqa: BLE001 - broker indisponível
        pytest.skip(f"RabbitMQ indisponível: {exc}")

    suffix = uuid.uuid4().hex
    exchange_name = f"sessionflow.test.evt.{suffix}"
    routing_key = f"events.test.{suffix}"
    sid = f"sess-{uuid.uuid4().hex[:8]}"

    try:
        channel = await conn.channel()
        # Exchange + fila EFÊMERAS (auto_delete), isoladas da topologia durável.
        exchange = await channel.declare_exchange(
            exchange_name,
            aio_pika.ExchangeType.DIRECT,
            durable=False,
            auto_delete=True,
        )
        queue = await channel.declare_queue("", durable=False, auto_delete=True)
        await queue.bind(exchange, routing_key=routing_key)

        try:
            doc = await emit_event(
                db,
                type="output",
                kind="success",
                session_id=sid,
                title="Saída capturada",
                desc="linha de output",
                channel=channel,
                exchange_name=exchange_name,
                routing_key=routing_key,
                collection=coll_name,
            )

            incoming = await asyncio.wait_for(queue.get(timeout=5), timeout=5)
            assert incoming is not None
            assert incoming.content_type == "application/json"
            payload = json.loads(incoming.body)
            assert payload["session_id"] == sid
            assert payload["type"] == "output"
            assert payload["kind"] == "success"
            assert payload["seq"] == doc["seq"]
            # ``at`` foi serializado p/ string ISO no payload.
            assert isinstance(payload["at"], str)
            await incoming.ack()

            # Também ficou persistido no Mongo.
            assert await db[coll_name].count_documents({"session_id": sid}) == 1
        finally:
            await queue.delete(if_unused=False, if_empty=False)
            await exchange.delete()
    finally:
        await conn.close()


async def test_discovery_emits_created_event(
    db,
    coll_name: str,
    runtime: TmuxRuntime,
    make_name: Callable[[], str],
    tmp_path,
) -> None:
    """``reconcile_once`` numa sessão nova grava um event ``created``."""
    sessions_coll = f"sessions_test_{uuid.uuid4().hex}"
    await db[sessions_coll].drop()

    discovery = Discovery(
        runtime,
        db,
        collection=sessions_coll,
        events_collection=coll_name,
        channel=None,
    )

    name = make_name()
    runtime.new_session(name, tmp_path)

    try:
        report = await discovery.reconcile_once()
        assert report.discovered >= 1

        created = await db[coll_name].find_one(
            {"session_id": name, "type": "created"}
        )
        assert created is not None
        assert created["kind"] == "info"
        assert created["seq"] >= 1
    finally:
        await db[sessions_coll].drop()
