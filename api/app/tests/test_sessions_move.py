"""Testes de integração de ``POST /sessions/{id}/move`` (mover sessão entre hosts).

Cobre cada linha da tabela de validação da spec
(``docs/superpowers/specs/2026-10-03-move-session-between-hosts-design.md``,
seção 1), o caminho feliz (grava ``moving`` e publica ``move_out`` na fila do
host de ORIGEM) e a limpeza de ``moving`` velho pelo ``resume``.

Isolamento: coleções ``sessions_test_<uuid>`` e ``host_directories_test_<uuid>``
dentro de ``sessionflow``; hosts com ids de teste únicos em ``worker_status``
(removidos no teardown); comandos caem na fila do host de TESTE (nunca na de um
worker real) e são drenados aqui.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import aio_pika
import pytest
import pytest_asyncio
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app
from app.publishers import command_publisher
from app.tests.test_sessions_create import (
    COMMANDS_QUEUE,
    EXCHANGE_NAME,
    _client,
    _mongo_host_uri,
    _rabbit_host_uri,
)

WORKER_STATUS = "worker_status"

@pytest_asyncio.fixture
async def env():
    """Settings isolados + hosts A (origem) e B (destino) online + helpers."""
    # A conexão AMQP cacheada no processo pode ter nascido no event loop de
    # OUTRO teste (já fechado) — força reconectar no loop deste.
    command_publisher._connection = None
    sessions_coll_name = f"sessions_test_{uuid.uuid4().hex}"
    dirs_coll_name = f"host_directories_test_{uuid.uuid4().hex}"
    settings = Settings(
        mongo_uri_host=_mongo_host_uri(),
        rabbitmq_uri_host=_rabbit_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_coll_name,
        host_directories_collection=dirs_coll_name,
        auth_email="",
    )
    host_a = f"test-move-a-{uuid.uuid4().hex[:8]}"
    host_b = f"test-move-b-{uuid.uuid4().hex[:8]}"
    mongo = AsyncIOMotorClient(settings.effective_mongo_uri)
    db = mongo[settings.mongo_db]
    now = datetime.now(UTC)

    connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queues = {}
    for h in (host_a, host_b):
        qname = f"{COMMANDS_QUEUE}.{h}"
        q = await channel.declare_queue(qname, durable=True)
        await q.bind(exchange, routing_key=qname)
        queues[h] = q

    class Env:
        pass

    e = Env()
    e.settings = settings
    e.db = db
    e.sessions = db[sessions_coll_name]
    e.dirs = db[dirs_coll_name]
    e.host_a = host_a
    e.host_b = host_b
    e.queues = queues

    async def seed(**overrides) -> str:
        doc = {
            "_id": ObjectId(),
            "tmux_name": f"mv-{uuid.uuid4().hex[:8]}",
            "agent_type": "claude",
            "claude_session_id": str(uuid.uuid4()),
            "status": "running",
            "work_dir": "/home/u/dev/sessionflow",
            "host_id": host_a,
            "created_at": now,
            "updated_at": now,
        }
        doc.update(overrides)
        doc = {k: v for k, v in doc.items() if v is not None}
        await e.sessions.insert_one(doc)
        return str(doc["_id"])

    async def add_dir(path: str, host_id: str | None = None) -> None:
        name = path.rstrip("/").rsplit("/", 1)[-1]
        await e.dirs.insert_one(
            {"path": path, "name": name, "host_id": host_id or host_b}
        )

    async def get_msg(host_id: str):
        for _ in range(20):
            msg = await queues[host_id].get(no_ack=False, fail=False)
            if msg is not None:
                await msg.ack()
                return json.loads(msg.body)
        return None

    async def move(session_id: str, target: str | None = None):
        app = create_app(settings=settings)
        async with await _client(app) as client:
            async with app.router.lifespan_context(app):
                return await client.post(
                    f"/sessions/{session_id}/move",
                    json={"target_host_id": target or host_b},
                )

    e.seed = seed
    e.add_dir = add_dir
    e.get_msg = get_msg
    e.move = move
    # Hosts falsos só entram no worker_status (coleção REAL, lida pela UI)
    # depois de tudo que pode falhar no setup: se o Rabbit recusar, o
    # finally abaixo nunca rodaria e eles vazavam como "máquinas offline".
    # Run anterior morto no meio (Ctrl+C/timeout) pula o finally — varre sobras.
    await db[WORKER_STATUS].delete_many({"_id": {"$regex": "^test-move-"}})
    try:
        for h in (host_a, host_b):
            await db[WORKER_STATUS].update_one(
                {"_id": h},
                {"$set": {"updated_at": now, "agents": {"claude": True}}},
                upsert=True,
            )
        yield e
    finally:
        for q in queues.values():
            for _ in range(50):
                msg = await q.get(no_ack=False, fail=False)
                if msg is None:
                    break
                await msg.ack()
            await q.delete(if_unused=False, if_empty=False)
        await connection.close()
        await db[WORKER_STATUS].delete_many({"_id": {"$in": [host_a, host_b]}})
        await e.sessions.drop()
        await e.dirs.drop()
        mongo.close()

@pytest.mark.integration
async def test_move_happy_path_publishes_move_out(env):
    sid = await env.seed()
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "accepted"

    msg = await env.get_msg(env.host_a)
    assert msg is not None, "move_out não publicado na fila do host A"
    assert msg["command_id"] == body["command_id"]
    assert msg["type"] == "move_out"
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    assert msg["payload"] == {
        "name": doc["tmux_name"],
        "session_id": sid,
        "target_host_id": env.host_b,
        "target_work_dir": "~/projects/sessionflow",
    }
    assert await env.get_msg(env.host_b) is None

    moving = doc["moving"]
    assert moving["target_host_id"] == env.host_b
    assert moving["target_work_dir"] == "~/projects/sessionflow"
    assert moving["phase"] == "exporting"
    assert moving["error"] is None
    assert moving["transfer_id"] is None
    assert isinstance(moving["started_at"], datetime)

@pytest.mark.integration
async def test_move_missing_session_404(env):
    resp = await env.move(str(ObjectId()))
    assert resp.status_code == 404

@pytest.mark.integration
async def test_move_session_without_host_404(env):
    sid = await env.seed(host_id=None)
    resp = await env.move(sid)
    assert resp.status_code == 404

@pytest.mark.integration
async def test_move_same_host_400(env):
    sid = await env.seed()
    resp = await env.move(sid, target=env.host_a)
    assert resp.status_code == 400
    assert "já está nesse host" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_target_offline_409(env):
    await env.db[WORKER_STATUS].update_one(
        {"_id": env.host_b},
        {"$set": {"updated_at": datetime.now(UTC) - timedelta(hours=1)}},
    )
    sid = await env.seed()
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 409
    assert "offline" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_unknown_target_409(env):
    sid = await env.seed()
    resp = await env.move(sid, target=f"test-move-nope-{uuid.uuid4().hex[:6]}")
    assert resp.status_code == 409
    assert "offline" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_non_claude_400(env):
    sid = await env.seed(agent_type="codex")
    resp = await env.move(sid)
    assert resp.status_code == 400
    assert "só claude" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_claude_without_session_id_400(env):
    sid = await env.seed(claude_session_id=None)
    resp = await env.move(sid)
    assert resp.status_code == 400
    assert "só claude" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_worktree_400(env):
    sid = await env.seed(worktree_path="/home/u/dev/sessionflow-clones/x")
    resp = await env.move(sid)
    assert resp.status_code == 400
    assert "worktree" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_child_with_pending_handoff_400(env):
    sid = await env.seed(parent="chefe")
    resp = await env.move(sid)
    assert resp.status_code == 400
    assert "handoff" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_child_already_notified_ok(env):
    sid = await env.seed(parent="chefe", parent_notified_at=datetime.now(UTC))
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text

@pytest.mark.integration
async def test_move_already_moving_409(env):
    sid = await env.seed(
        moving={
            "target_host_id": env.host_b,
            "target_work_dir": "~/x",
            "phase": "importing",
            "error": None,
            "started_at": datetime.now(UTC) - timedelta(minutes=1),
            "transfer_id": "t1",
        }
    )
    resp = await env.move(sid)
    assert resp.status_code == 409
    assert "já está movendo" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_stale_moving_allows_new_move(env):
    sid = await env.seed(
        moving={
            "target_host_id": env.host_b,
            "target_work_dir": "~/x",
            "phase": "importing",
            "error": None,
            "started_at": datetime.now(UTC) - timedelta(minutes=10),
            "transfer_id": "t1",
        }
    )
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    assert doc["moving"]["phase"] == "exporting"
    assert doc["moving"]["transfer_id"] is None

@pytest.mark.integration
async def test_move_failed_moving_allows_new_move(env):
    """``phase = failed`` não é transferência em andamento — não bloqueia."""
    sid = await env.seed(
        moving={
            "target_host_id": env.host_b,
            "target_work_dir": "~/x",
            "phase": "failed",
            "error": "boom",
            "started_at": datetime.now(UTC) - timedelta(minutes=1),
            "transfer_id": None,
        }
    )
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text

@pytest.mark.integration
async def test_move_target_without_claude_cli_409(env):
    await env.db[WORKER_STATUS].update_one(
        {"_id": env.host_b}, {"$set": {"agents": {"claude": False, "codex": True}}}
    )
    sid = await env.seed()
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 409
    assert "claude" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_target_without_agents_field_does_not_block(env):
    """Worker antigo (sem ``agents`` no heartbeat) não bloqueia."""
    await env.db[WORKER_STATUS].update_one(
        {"_id": env.host_b}, {"$unset": {"agents": ""}}
    )
    sid = await env.seed()
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text

@pytest.mark.integration
async def test_move_dir_not_found_no_work_dir_409(env):
    """Sem match no índice E sem work_dir na origem → 409 (não tem como inferir)."""
    sid = await env.seed(work_dir="")
    await env.add_dir("~/projects/outra-coisa")
    resp = await env.move(sid)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "não encontrada" in detail
    assert env.host_b in detail
    assert "work_dir" in detail  # explica o motivo

@pytest.mark.integration
async def test_move_dir_not_found_falls_back_to_source_work_dir(env):
    """Sem match no índice MAS work_dir presente na origem → 202: usa o
    work_dir da origem como destino; o worker faz auto-clone do git_origin."""
    sid = await env.seed()
    await env.add_dir("~/projects/outra-coisa")
    await env.add_dir("/home/u/dev/sessionflow", host_id=env.host_a)
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    msg = await env.get_msg(env.host_a)
    assert msg is not None and msg["type"] == "move_out"
    assert msg["payload"]["target_work_dir"] == "/home/u/dev/sessionflow"
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    assert doc["moving"]["target_work_dir"] == "/home/u/dev/sessionflow"

@pytest.mark.integration
async def test_move_dir_not_found_falls_back_preserves_tilde(env):
    """Fallback preserva o ``~`` (o worker destino expande)."""
    sid = await env.seed(work_dir="~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text
    msg = await env.get_msg(env.host_a)
    assert msg["payload"]["target_work_dir"] == "~/projects/sessionflow"

@pytest.mark.integration
async def test_move_dir_ambiguous_409(env):
    sid = await env.seed()
    await env.add_dir("~/projects/sessionflow")
    await env.add_dir("~/old/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 409
    assert "ambígua" in resp.json()["detail"]

@pytest.mark.integration
async def test_move_dir_match_is_exact_basename(env):
    """``sessionflow-clones`` não casa com ``sessionflow``."""
    sid = await env.seed()
    await env.add_dir("~/projects/sessionflow")
    await env.add_dir("~/projects/sessionflow-clones")
    await env.add_dir("~/projects/Sessionflow2")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text

@pytest.mark.integration
async def test_move_target_has_active_same_tmux_name_409(env):
    sid = await env.seed()
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    await env.seed(tmux_name=doc["tmux_name"], host_id=env.host_b, status="running")
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 409
    assert doc["tmux_name"] in resp.json()["detail"]

@pytest.mark.integration
async def test_move_target_stopped_same_tmux_name_ok(env):
    sid = await env.seed()
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    await env.seed(tmux_name=doc["tmux_name"], host_id=env.host_b, status="stopped")
    await env.add_dir("~/projects/sessionflow")
    resp = await env.move(sid)
    assert resp.status_code == 202, resp.text

@pytest.mark.integration
async def test_move_rejected_does_not_set_moving_nor_publish(env):
    sid = await env.seed(agent_type="codex")
    resp = await env.move(sid)
    assert resp.status_code == 400
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    assert "moving" not in doc
    assert await env.get_msg(env.host_a) is None

async def _resume(env, sid):
    app = create_app(settings=env.settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            return await client.post(f"/sessions/{sid}/resume")

@pytest.mark.integration
async def test_resume_clears_stale_moving(env):
    sid = await env.seed(
        status="stopped",
        moving={
            "target_host_id": env.host_b,
            "target_work_dir": "~/x",
            "phase": "importing",
            "error": None,
            "started_at": datetime.now(UTC) - timedelta(minutes=10),
            "transfer_id": "t1",
        },
    )
    resp = await _resume(env, sid)
    assert resp.status_code == 202
    msg = await env.get_msg(env.host_a)
    assert msg["type"] == "resume"
    assert msg["payload"] == {"name": (await env.sessions.find_one(
        {"_id": ObjectId(sid)}))["tmux_name"]}
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    assert "moving" not in doc

@pytest.mark.integration
async def test_resume_keeps_fresh_moving(env):
    sid = await env.seed(
        status="stopped",
        moving={
            "target_host_id": env.host_b,
            "target_work_dir": "~/x",
            "phase": "importing",
            "error": None,
            "started_at": datetime.now(UTC) - timedelta(minutes=1),
            "transfer_id": "t1",
        },
    )
    resp = await _resume(env, sid)
    assert resp.status_code == 202
    doc = await env.sessions.find_one({"_id": ObjectId(sid)})
    assert doc["moving"]["phase"] == "importing"

@pytest.mark.integration
async def test_session_out_exposes_move_fields(env):
    at = datetime.now(UTC)
    sid = await env.seed(
        moved_at=at,
        move_history=[{"from": env.host_a, "to": env.host_b, "at": at}],
        moving={
            "target_host_id": env.host_b,
            "target_work_dir": "~/x",
            "phase": "failed",
            "error": "Há mudanças não commitadas",
            "started_at": at,
            "transfer_id": None,
        },
    )
    app = create_app(settings=env.settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["moving"]["phase"] == "failed"
    assert body["moving"]["error"] == "Há mudanças não commitadas"
    assert body["moved_at"] is not None
    assert body["move_history"][0]["to"] == env.host_b
    assert body["moving"]["started_at"].endswith(("Z", "+00:00"))
