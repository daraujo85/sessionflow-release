"""Integration tests for comandos programados (scheduled recurring input).

Covers CRUD (`POST/GET/PATCH/DELETE`) and the background scheduler loop
(``app.scheduler.run_scheduler_forever``) actually firing a due command as
an ``input`` on ``sessionflow.commands`` — same wire format as the manual
``POST /sessions/{id}/input`` (see ``test_sessions_input.py``).

Runs on the host against the docker stack (Mongo + RabbitMQ healthy).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aio_pika
import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app

EXCHANGE_NAME = "sessionflow"
COMMANDS_QUEUE = "sessionflow.commands"

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _env_value(key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return None


def _mongo_host_uri() -> str:
    return _env_value("MONGO_URI_HOST") or (
        "mongodb://sessionflow:882938ade9f4298f59daccc2dc5add74"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow"
    )


def _rabbit_host_uri() -> str:
    return _env_value("RABBITMQ_URI_HOST") or "amqp://guest:guest@127.0.0.1:5672/"


def _host_settings(sessions_collection: str, schedules_collection: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        rabbitmq_uri_host=_rabbit_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_collection,
        scheduled_commands_collection=schedules_collection,
        # Poll rápido pro teste do loop não esperar 20s à toa.
        scheduler_poll_seconds=1,
        # Desliga o middleware de auth p/ o cliente de teste (sem token) — sem
        # isso, um shell dev com SESSIONFLOW_EMAIL/PASSWORD exportado no
        # ambiente faz o Settings() herdar credenciais reais e todo request
        # sem Authorization vira 401 (mesmo comportamento pré-existente em
        # test_sessions_input.py neste tipo de shell).
        auth_email="",
        auth_password="",
    )


@pytest_asyncio.fixture
async def settings():
    sessions_collection = f"sessions_test_{uuid.uuid4().hex}"
    schedules_collection = f"scheduled_commands_test_{uuid.uuid4().hex}"
    s = _host_settings(sessions_collection, schedules_collection)

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][sessions_collection].drop()
        await client[s.mongo_db][schedules_collection].drop()
        client.close()


@pytest_asyncio.fixture
async def drain_commands(settings):
    connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queue = await channel.declare_queue(COMMANDS_QUEUE, durable=True)
    await queue.bind(exchange, routing_key=COMMANDS_QUEUE)

    drained: list[dict] = []

    async def fetch_by_type(session_name: str, msg_type: str, attempts: int = 50) -> dict | None:
        for _ in range(attempts):
            msg = await queue.get(no_ack=False, fail=False)
            if msg is None:
                await asyncio.sleep(0.2)
                continue
            body = json.loads(msg.body)
            await msg.ack()
            drained.append(body)
            if body.get("type") == msg_type and body.get("payload", {}).get("name") == session_name:
                return body
        return None

    try:
        yield fetch_by_type
    finally:
        for _ in range(50):
            msg = await queue.get(no_ack=False, fail=False)
            if msg is None:
                break
            await msg.ack()
        await connection.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _seed_doc(status: str, name: str) -> dict:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": name,
        "display_name": name,
        "agent_type": "claude",
        "status": status,
        "work_dir": "/tmp/work",
        "created_at": now,
        "updated_at": now,
    }


async def _seed(settings, status: str, name: str) -> str:
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    doc = _seed_doc(status, name)
    await collection.insert_one(doc)
    client.close()
    return str(doc["_id"])


@pytest.mark.integration
async def test_create_list_schedule(settings):
    session_id = await _seed(settings, "running", f"sched-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            create_resp = await client.post(
                f"/sessions/{session_id}/schedules",
                json={"text": "rode a skill X", "interval_seconds": 3600},
            )
            assert create_resp.status_code == 201
            created = create_resp.json()
            assert created["text"] == "rode a skill X"
            assert created["interval_seconds"] == 3600
            assert created["enabled"] is True
            assert created["session_id"] == session_id

            list_resp = await client.get(f"/sessions/{session_id}/schedules")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]


@pytest.mark.integration
async def test_create_unknown_session_not_found(settings):
    missing_id = str(ObjectId())
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{missing_id}/schedules",
                json={"text": "x", "interval_seconds": 3600},
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_create_rejects_short_interval(settings):
    session_id = await _seed(settings, "running", f"sched-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{session_id}/schedules",
                json={"text": "x", "interval_seconds": 5},
            )
    assert resp.status_code == 422


@pytest.mark.integration
async def test_patch_pause_resume_and_edit(settings):
    session_id = await _seed(settings, "running", f"sched-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(
                    f"/sessions/{session_id}/schedules",
                    json={"text": "a", "interval_seconds": 3600},
                )
            ).json()
            schedule_id = created["id"]

            paused = (
                await client.patch(f"/schedules/{schedule_id}", json={"enabled": False})
            ).json()
            assert paused["enabled"] is False

            edited = (
                await client.patch(
                    f"/schedules/{schedule_id}",
                    json={"text": "b", "interval_seconds": 7200},
                )
            ).json()
            assert edited["text"] == "b"
            assert edited["interval_seconds"] == 7200
            assert edited["enabled"] is False  # patch não mexeu, continua pausado

            resumed = (
                await client.patch(f"/schedules/{schedule_id}", json={"enabled": True})
            ).json()
            assert resumed["enabled"] is True


@pytest.mark.integration
async def test_patch_unknown_not_found(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.patch(
                f"/schedules/{ObjectId()}", json={"enabled": False}
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_delete_schedule(settings):
    session_id = await _seed(settings, "running", f"sched-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(
                    f"/sessions/{session_id}/schedules",
                    json={"text": "a", "interval_seconds": 3600},
                )
            ).json()

            del_resp = await client.delete(f"/schedules/{created['id']}")
            assert del_resp.status_code == 204

            missing_resp = await client.delete(f"/schedules/{created['id']}")
            assert missing_resp.status_code == 404

            list_resp = await client.get(f"/sessions/{session_id}/schedules")
    assert list_resp.json()["total"] == 0


@pytest.mark.integration
async def test_scheduler_loop_fires_due_command(settings, drain_commands):
    """O loop de background (lifespan) dispara um comando `input` vencido."""
    name = f"sched-{uuid.uuid4().hex[:8]}"
    session_id = await _seed(settings, "running", name)
    text = f"echo scheduled-{uuid.uuid4().hex[:6]}"

    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            # Menor intervalo aceito (60s) — mas seedamos o doc diretamente com
            # next_run_at já vencido, pra não esperar 60s pelo 1º disparo.
            create_resp = await client.post(
                f"/sessions/{session_id}/schedules",
                json={"text": text, "interval_seconds": 60},
            )
            schedule_id = create_resp.json()["id"]

            mongo = AsyncIOMotorClient(settings.effective_mongo_uri)
            collection = mongo[settings.mongo_db][settings.scheduled_commands_collection]
            await collection.update_one(
                {"_id": ObjectId(schedule_id)},
                {"$set": {"next_run_at": datetime.now(UTC)}},
            )
            mongo.close()

            # scheduler_poll_seconds=1 (fixture) — dá tempo de rodar >=1 tick.
            msg = await drain_commands(name, "input")
            assert msg is not None, "scheduler não disparou o comando a tempo"
            assert msg["payload"]["text"] == text
            assert msg["payload"]["enter"] is True

            get_resp = await client.get(f"/sessions/{session_id}/schedules")
    updated = get_resp.json()["items"][0]
    assert updated["last_run_at"] is not None
    assert updated["last_error"] is None
