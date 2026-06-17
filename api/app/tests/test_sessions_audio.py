"""Integration tests for ``POST /sessions/{id}/audio`` (DASH-14).

Covers:
- happy path -> file persisted on disk, ``uploads`` doc created, ``audio``
  command published carrying the stored path and tmux name;
- unknown id -> 404 (no file/doc/command);
- distinct uploads land in per-session directories with unique names.

Runs on the host against the docker stack (Mongo + RabbitMQ healthy).

Mongo: the user can only access the ``sessionflow`` database, so we use an
isolated sessions collection ``sessions_test_<uuid>`` and an isolated uploads
collection ``uploads_test_<uuid>`` within ``sessionflow``; both dropped on
teardown.

Uploads dir: injected as pytest's ``tmp_path`` so nothing touches the real
``/data/uploads`` volume.

RabbitMQ: we publish to the real ``sessionflow.commands`` queue; the consume
fixture drains/ack-removes any message produced during a test and purges
stragglers on teardown.
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


def _host_settings(
    sessions_collection: str, uploads_collection: str, uploads_dir: str
) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        rabbitmq_uri_host=_rabbit_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_collection,
        uploads_collection=uploads_collection,
        uploads_dir=uploads_dir,
    )


@pytest_asyncio.fixture
async def settings(tmp_path):
    """Isolated sessions + uploads collections and a tmp uploads dir."""
    sessions_collection = f"sessions_test_{uuid.uuid4().hex}"
    uploads_collection = f"uploads_test_{uuid.uuid4().hex}"
    s = _host_settings(sessions_collection, uploads_collection, str(tmp_path))

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][sessions_collection].drop()
        await client[s.mongo_db][uploads_collection].drop()
        client.close()


@pytest_asyncio.fixture
async def drain_commands(settings):
    """Consume/ack messages left in ``sessionflow.commands`` after a test."""
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


async def _uploads_doc(settings, upload_id: str) -> dict | None:
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.uploads_collection]
    doc = await collection.find_one({"_id": ObjectId(upload_id)})
    client.close()
    return doc


@pytest.mark.integration
async def test_audio_upload_accepted(settings, drain_commands):
    name = f"aud-{uuid.uuid4().hex[:8]}"
    session_id = await _seed(settings, "running", name)
    audio_bytes = b"RIFF....WAVE-dummy-" + uuid.uuid4().hex.encode()

    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{session_id}/audio",
                files={"file": ("clip.wav", audio_bytes, "audio/wav")},
            )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["command_id"]
    assert body["upload_id"]

    # File persisted on disk under the per-session directory.
    session_dir = Path(settings.uploads_dir) / session_id
    files = list(session_dir.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".wav"
    assert files[0].read_bytes() == audio_bytes

    # uploads doc created with expected fields.
    doc = await _uploads_doc(settings, body["upload_id"])
    assert doc is not None
    assert doc["session_id"] == session_id
    assert doc["kind"] == "audio"
    assert doc["status"] == "received"
    assert doc["path"] == str(files[0])

    # audio command published carrying name + stored path + upload_id.
    msg = await drain_commands(body["command_id"])
    assert msg is not None, "audio command not found on sessionflow.commands"
    assert msg["type"] == "audio"
    assert msg["payload"]["name"] == name
    assert msg["payload"]["path"] == str(files[0])
    assert msg["payload"]["upload_id"] == body["upload_id"]


@pytest.mark.integration
async def test_audio_upload_missing_not_found(settings):
    missing_id = str(ObjectId())
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{missing_id}/audio",
                files={"file": ("clip.wav", b"dummy", "audio/wav")},
            )
    assert resp.status_code == 404
    # Nothing written to disk for the missing session.
    assert not (Path(settings.uploads_dir) / missing_id).exists()


@pytest.mark.integration
async def test_audio_upload_unique_paths(settings, drain_commands):
    name = f"aud-{uuid.uuid4().hex[:8]}"
    session_id = await _seed(settings, "running", name)

    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp1 = await client.post(
                f"/sessions/{session_id}/audio",
                files={"file": ("a.mp3", b"one", "audio/mpeg")},
            )
            resp2 = await client.post(
                f"/sessions/{session_id}/audio",
                files={"file": ("b.mp3", b"two", "audio/mpeg")},
            )

    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp1.json()["upload_id"] != resp2.json()["upload_id"]

    session_dir = Path(settings.uploads_dir) / session_id
    files = sorted(session_dir.iterdir())
    assert len(files) == 2
    assert all(f.suffix == ".mp3" for f in files)

    # Drain both published commands so teardown is clean.
    assert await drain_commands(resp1.json()["command_id"]) is not None
    assert await drain_commands(resp2.json()["command_id"]) is not None
