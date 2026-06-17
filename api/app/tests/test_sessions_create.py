"""Integration tests for POST /sessions (TMUX-05/06/07).

Runs on the host against the docker stack (Mongo + RabbitMQ healthy).

Mongo: the user can only access the ``sessionflow`` database, so we seed an
isolated collection ``sessions_test_<uuid>`` inside ``sessionflow`` (injected
via ``sessions_collection``) and drop it on teardown.

RabbitMQ: we publish to the real ``sessionflow.commands`` queue. To avoid
leaking messages into the queue the Worker consumes, the consume fixture
drains and ack-removes any message produced during a test (matching by the
``command_id`` the endpoint returns), and purges remaining test messages.
"""

from __future__ import annotations

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


# Repo root .env (two levels above the ``api`` package dir).
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _env_value(key: str) -> str | None:
    """Read ``key`` from the process env, falling back to the repo ``.env``."""
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


def _host_settings(collection_name: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        rabbitmq_uri_host=_rabbit_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=collection_name,
    )


@pytest_asyncio.fixture
async def settings():
    """Isolated session collection; dropped on teardown."""
    collection_name = f"sessions_test_{uuid.uuid4().hex}"
    s = _host_settings(collection_name)

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][collection_name].drop()
        client.close()


@pytest_asyncio.fixture
async def drain_commands(settings):
    """Consume/ack messages left in ``sessionflow.commands`` after a test.

    Yields a helper that fetches the published message matching a given
    ``command_id`` (acking it so it does not reach the Worker), then on
    teardown purges any other test message this run may have published.
    """
    connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    channel = await connection.channel()
    # Ensure topology exists (idempotent) so the queue is present to consume.
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queue = await channel.declare_queue(COMMANDS_QUEUE, durable=True)
    await queue.bind(exchange, routing_key=COMMANDS_QUEUE)

    drained: list[dict] = []

    async def fetch(command_id: str, attempts: int = 50) -> dict | None:
        """Pull messages until the one with ``command_id`` is found (ack all)."""
        for _ in range(attempts):
            msg = await queue.get(no_ack=False, fail=False)
            if msg is None:
                continue
            body = json.loads(msg.body)
            await msg.ack()
            drained.append(body)
            if body.get("command_id") == command_id:
                return body
        return None

    try:
        yield fetch
    finally:
        # Best-effort: remove any straggler test messages so we don't leak.
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


@pytest.mark.integration
async def test_create_valid_publishes_command(settings, drain_commands):
    app = create_app(settings=settings)
    payload = {
        "name": f"sess-{uuid.uuid4().hex[:8]}",
        "agent_type": "claude",
        "work_dir": "/tmp/work",
        "model": "opus",
        "effort": "high",
    }
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    command_id = body["command_id"]
    assert command_id

    msg = await drain_commands(command_id)
    assert msg is not None, "command not found on sessionflow.commands"
    assert msg["type"] == "create"
    assert msg["payload"]["name"] == payload["name"]
    assert msg["payload"]["agent_type"] == "claude"
    assert msg["payload"]["effort"] == "high"
    assert "requested_at" in msg


@pytest.mark.integration
async def test_create_gemini_ignores_effort(settings, drain_commands):
    app = create_app(settings=settings)
    payload = {
        "name": f"sess-{uuid.uuid4().hex[:8]}",
        "agent_type": "gemini",
        "work_dir": "/tmp/work",
        "model": "gemini-pro",
        "effort": "high",
    }
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]

    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["agent_type"] == "gemini"
    assert msg["payload"]["effort"] is None


@pytest.mark.integration
async def test_create_duplicate_active_name_conflicts(settings, drain_commands):
    name = f"dup-{uuid.uuid4().hex[:8]}"

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    await collection.insert_one(_seed_doc("running", name))
    client.close()

    app = create_app(settings=settings)
    payload = {"name": name, "agent_type": "claude", "work_dir": "/tmp/work"}
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)

    assert resp.status_code == 409
    # Nothing should have been published for this name.
    fake_id = str(uuid.uuid4())
    leaked = await drain_commands(fake_id, attempts=5)
    assert leaked is None


@pytest.mark.integration
async def test_create_duplicate_stopped_name_allowed(settings, drain_commands):
    name = f"stp-{uuid.uuid4().hex[:8]}"

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    await collection.insert_one(_seed_doc("stopped", name))
    client.close()

    app = create_app(settings=settings)
    payload = {"name": name, "agent_type": "claude", "work_dir": "/tmp/work"}
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == name


@pytest.mark.integration
async def test_create_missing_field_unprocessable(settings):
    app = create_app(settings=settings)
    # Missing work_dir.
    payload = {"name": "x", "agent_type": "claude"}
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)
    assert resp.status_code == 422


@pytest.mark.integration
async def test_create_invalid_agent_type_unprocessable(settings):
    app = create_app(settings=settings)
    payload = {"name": "x", "agent_type": "bogus", "work_dir": "/tmp/work"}
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post("/sessions", json=payload)
    assert resp.status_code == 422
