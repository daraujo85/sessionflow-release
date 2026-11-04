"""Testes do reaper de PONTES DE COMANDO (sessões ``agent_type="command"``).

A ponte é a sessão "comando" que faz a ponte com um host-alvo (``sf command
<host>`` reusa sempre a mesma). Ela é VOLÁTIL: morre por TTL ocioso, em cascata
quando a sessão dona acaba, e nunca passa de uma por host.

O tick roda contra o Mongo de teste (coleção isolada, mesmo host da stack) e
com o ``publish_command`` trocado por um coletor — o que importa aqui é a
DECISÃO (purga ou não) e o PAYLOAD publicado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.repositories.sessions_repo import SessionsRepository
from app.scheduler import _command_bridges_tick
from app.tests.test_sessions_create import _TEST_HOST_ID, _client

HOST = "duck-id"
OTHER_HOST = "mac-id"


@pytest.fixture
def published(monkeypatch):
    """Coleta o que o tick publica (no lugar do RabbitMQ)."""
    calls: list[dict] = []

    async def fake(settings, type=None, payload=None, host_id=None, **kwargs):
        calls.append({"type": type, "payload": payload, "host_id": host_id})
        return "cmd-1"

    monkeypatch.setattr("app.scheduler.publish_command", fake)
    return calls


def _bridge(**over):
    now = datetime.now(UTC)
    doc = {
        "_id": ObjectId(),
        "tmux_name": "cmd-duck",
        "display_name": "cmd-duck",
        "agent_type": "command",
        "status": "running",
        "host_id": HOST,
        # work_dir da ponte = HOME do host. Se o purge mandar isso, o worker
        # roda `shutil.rmtree` no HOME — por isso o teste do payload.
        "work_dir": "/home/diego",
        "last_activity_at": now,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(over)
    return doc


def _owner_doc(name: str, status: str = "running"):
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": name,
        "display_name": name,
        "agent_type": "claude",
        "status": status,
        "work_dir": "/tmp/work",
        "last_activity_at": now,
        "created_at": now,
        "updated_at": now,
    }


async def _run(settings, docs):
    """Semeia os docs, roda o tick e devolve (repo, purgadas)."""
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    db = client[settings.mongo_db]
    repo = SessionsRepository(db, settings.sessions_collection)
    for doc in docs:
        await db[settings.sessions_collection].insert_one(doc)
    try:
        await _command_bridges_tick(db, repo, settings)
        alive = {
            d["tmux_name"]
            async for d in db[settings.sessions_collection].find({})
        }
        return alive
    finally:
        client.close()


@pytest.mark.integration
async def test_idle_bridge_is_purged(settings, published):
    """Sem uso há mais que o TTL → a ponte morre (doc + comando delete)."""
    bridge = _bridge(last_activity_at=datetime.now(UTC) - timedelta(hours=2))
    alive = await _run(settings, [bridge])

    assert "cmd-duck" not in alive
    assert len(published) == 1
    assert published[0]["type"] == "delete"
    assert published[0]["payload"]["name"] == "cmd-duck"
    assert published[0]["host_id"] == HOST


@pytest.mark.integration
async def test_purge_payload_never_carries_work_dir(settings, published):
    """⚠️ O ``work_dir`` da ponte é o HOME do host.

    Mandá-lo no payload faz o worker apagar a pasta inteira
    (``shutil.rmtree``). O purge da ponte manda SÓ o nome.
    """
    bridge = _bridge(last_activity_at=datetime.now(UTC) - timedelta(hours=2))
    await _run(settings, [bridge])

    payload = published[0]["payload"]
    assert "work_dir" not in payload
    assert not payload.get("ephemeral")


@pytest.mark.integration
async def test_fresh_bridge_survives(settings, published):
    bridge = _bridge()
    alive = await _run(settings, [bridge])

    assert "cmd-duck" in alive
    assert published == []


@pytest.mark.integration
async def test_bridge_dies_with_its_owner(settings, published):
    """Cascata: dona morta → ponte morta (mesmo sem TTL vencido)."""
    bridge = _bridge(owner="sess-morta")
    alive = await _run(settings, [bridge])

    assert "cmd-duck" not in alive
    assert published[0]["type"] == "delete"


@pytest.mark.integration
async def test_bridge_survives_while_owner_is_alive(settings, published):
    owner = _owner_doc("sess-viva", status="running")
    bridge = _bridge(owner="sess-viva")
    alive = await _run(settings, [owner, bridge])

    assert "cmd-duck" in alive
    assert published == []


@pytest.mark.integration
async def test_bridge_dies_when_owner_stopped(settings, published):
    """Dona parada (status final) = dona morta → cascata."""
    owner = _owner_doc("sess-viva", status="stopped")
    bridge = _bridge(owner="sess-viva")
    alive = await _run(settings, [owner, bridge])

    assert "cmd-duck" not in alive


@pytest.mark.integration
async def test_only_one_bridge_per_host(settings, published):
    """Dedupe: duas pontes vivas no mesmo host → a menos recente morre."""
    now = datetime.now(UTC)
    older = _bridge(tmux_name="cmd-duck-old", last_activity_at=now - timedelta(minutes=5))
    newer = _bridge(tmux_name="cmd-duck-new", last_activity_at=now)
    alive = await _run(settings, [older, newer])

    assert "cmd-duck-new" in alive
    assert "cmd-duck-old" not in alive


@pytest.mark.integration
async def test_one_bridge_per_host_isolated(settings, published):
    """Pontes de hosts diferentes convivem (uma por host)."""
    a = _bridge(tmux_name="cmd-duck", host_id=HOST)
    b = _bridge(tmux_name="cmd-mac", host_id=OTHER_HOST)
    alive = await _run(settings, [a, b])

    assert alive == {"cmd-duck", "cmd-mac"}
    assert published == []


@pytest.mark.integration
async def test_keep_alive_bridge_survives_everything(settings, published):
    """Serviço (keep_alive): fora de TTL, cascata e dedupe."""
    now = datetime.now(UTC)
    svc = _bridge(
        tmux_name="tmux-sf-phone-relay",
        keep_alive=True,
        owner="sess-morta",
        last_activity_at=now - timedelta(days=1),
    )
    other = _bridge(tmux_name="cmd-duck", last_activity_at=now)
    alive = await _run(settings, [svc, other])

    assert "tmux-sf-phone-relay" in alive
    assert "cmd-duck" in alive
    assert published == []


# --------------------------------------------------------------------------- #
# UMA ponte por host: a API recusa a 2ª ponte do mesmo host
# --------------------------------------------------------------------------- #


async def _seed(settings, docs):
    """Semeia docs na coleção de teste e devolve o cliente do Mongo."""
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    db = client[settings.mongo_db]
    for doc in docs:
        await db[settings.sessions_collection].insert_one(doc)
    return client, db


@pytest.mark.integration
async def test_create_command_conflicts_with_live_bridge(settings, drain_commands):
    """Já existe ponte viva no host → a 2ª criação é RECUSADA (409).

    O detalhe é ESTRUTURADO (``code`` + ``session_id``) pro front ABRIR a
    ponte existente em vez de mostrar "erro".
    """
    from app.main import create_app

    bridge = _bridge(tmux_name="cmd-duck", host_id=_TEST_HOST_ID)
    client, db = await _seed(settings, [bridge])
    try:
        app = create_app(settings=settings)
        payload = {
            "name": "outra-ponte",
            "agent_type": "command",
            "host_id": _TEST_HOST_ID,
        }
        async with await _client(app) as http:
            async with app.router.lifespan_context(app):
                resp = await http.post("/sessions", json=payload)
    finally:
        client.close()

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "command_bridge_exists"
    assert detail["session_id"] == str(bridge["_id"])
    assert detail["session_name"] == "cmd-duck"


@pytest.mark.integration
async def test_create_command_allowed_when_bridge_is_service(settings, drain_commands):
    """``keep_alive`` (serviço) não ocupa o slot da ponte do host."""
    from app.main import create_app

    bridge = _bridge(tmux_name="cmd-duck", host_id=_TEST_HOST_ID)
    client, db = await _seed(settings, [bridge])
    try:
        app = create_app(settings=settings)
        payload = {
            "name": "servico-duck",
            "agent_type": "command",
            "host_id": _TEST_HOST_ID,
            "keep_alive": True,
        }
        async with await _client(app) as http:
            async with app.router.lifespan_context(app):
                resp = await http.post("/sessions", json=payload)
        command_id = resp.json()["command_id"]
        msg = await drain_commands(command_id)
    finally:
        client.close()

    assert resp.status_code == 202
    assert msg is not None
    assert msg["payload"]["keep_alive"] is True


@pytest.mark.integration
async def test_recreated_bridge_loses_stale_owner(settings, drain_commands):
    """Ponte recriada com o MESMO nome não herda a dona (morta) da vida velha.

    O worker faz upsert pelo ``tmux_name`` → sem o reset, a ponte nova nasceria
    com ``owner`` de uma sessão que já morreu e morreria em cascata no tick
    seguinte.
    """
    from app.main import create_app

    old = _bridge(
        tmux_name="cmd-duck",
        host_id=_TEST_HOST_ID,
        status="stopped",
        owner="sess-morta",
        keep_alive=True,
    )
    client, db = await _seed(settings, [old])
    try:
        app = create_app(settings=settings)
        payload = {
            "name": "cmd-duck",
            "agent_type": "command",
            "host_id": _TEST_HOST_ID,
        }
        async with await _client(app) as http:
            async with app.router.lifespan_context(app):
                resp = await http.post("/sessions", json=payload)
        doc = await db[settings.sessions_collection].find_one({"tmux_name": "cmd-duck"})
    finally:
        client.close()

    assert resp.status_code == 202
    assert doc["owner"] is None
    assert not doc.get("keep_alive")
