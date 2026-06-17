"""Integration tests for ``POST /sessions/{id}/input`` (DASH-13).

Covers:
- happy path -> publishes an ``input`` command carrying the text;
- unknown id -> 404 (no command published);
- empty/whitespace text -> 422 (no command published).

Runs on the host against the docker stack (Mongo + RabbitMQ healthy).

Mongo: the user can only access the ``sessionflow`` database, so we seed an
isolated collection ``sessions_test_<uuid>`` inside ``sessionflow`` (injected
via ``sessions_collection``) and drop it on teardown.

RabbitMQ: we publish to the real ``sessionflow.commands`` queue; the consume
fixture drains/ack-removes any message produced during a test (matching by the
``command_id`` the endpoint returns) and purges stragglers on teardown.
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
    """Insert a session doc and return its id (string)."""
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    doc = _seed_doc(status, name)
    await collection.insert_one(doc)
    client.close()
    return str(doc["_id"])


@pytest.mark.integration
async def test_input_publishes_command(settings, drain_commands):
    name = f"inp-{uuid.uuid4().hex[:8]}"
    session_id = await _seed(settings, "running", name)
    text = f"echo hello-{uuid.uuid4().hex[:6]}"

    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{session_id}/input", json={"text": text}
            )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    command_id = body["command_id"]

    msg = await drain_commands(command_id)
    assert msg is not None, "command not found on sessionflow.commands"
    assert msg["type"] == "input"
    assert msg["payload"] == {"name": name, "text": text}


@pytest.mark.integration
async def test_input_trims_text(settings, drain_commands):
    name = f"inp-{uuid.uuid4().hex[:8]}"
    session_id = await _seed(settings, "running", name)

    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{session_id}/input", json={"text": "  ls -la  "}
            )

    assert resp.status_code == 202
    msg = await drain_commands(resp.json()["command_id"])
    assert msg is not None
    assert msg["type"] == "input"
    assert msg["payload"] == {"name": name, "text": "ls -la"}


@pytest.mark.integration
async def test_input_missing_not_found(settings):
    missing_id = str(ObjectId())
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{missing_id}/input", json={"text": "whatever"}
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_input_empty_text_unprocessable(settings):
    session_id = await _seed(settings, "running", f"inp-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            # Empty string -> rejected by min_length=1 (422).
            resp_empty = await client.post(
                f"/sessions/{session_id}/input", json={"text": ""}
            )
            # Whitespace-only -> rejected by the strip() guard (422).
            resp_blank = await client.post(
                f"/sessions/{session_id}/input", json={"text": "   "}
            )
    assert resp_empty.status_code == 422
    assert resp_blank.status_code == 422
