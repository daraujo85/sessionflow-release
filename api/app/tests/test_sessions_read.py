"""Integration tests for GET /sessions and GET /sessions/{id}.

Runs on the host against the docker stack. The Mongo user can only access the
``sessionflow`` database, so we do NOT create a test DB. Instead we seed an
isolated collection ``sessions_test_<uuid>`` inside ``sessionflow`` (injected
via the ``sessions_collection`` setting) and drop it on teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app


def _mongo_host_uri() -> str:
    return os.environ.get(
        "MONGO_URI_HOST",
        "mongodb://sessionflow:882938ade9f4298f59daccc2dc5add74"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow",
    )


def _host_settings(collection_name: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=collection_name,
    )


def _doc(status: str, display_name: str) -> dict:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": f"tmux-{display_name}",
        "display_name": display_name,
        "agent_type": "claude",
        "model": "opus",
        "effort": "high",
        "work_dir": "/tmp/work",
        "status": status,
        "origin": "sessionflow",
        "tmux_session_id": "$1",
        "agent_pid": 1234,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }


@pytest_asyncio.fixture
async def seeded():
    """Seed an isolated collection with 3 docs; drop it on teardown."""
    collection_name = f"sessions_test_{uuid.uuid4().hex}"
    settings = _host_settings(collection_name)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][collection_name]

    docs = [
        _doc("running", "alpha"),
        _doc("running", "beta"),
        _doc("completed", "gamma"),
    ]
    await collection.insert_many(docs)

    try:
        yield {"settings": settings, "docs": docs}
    finally:
        await collection.drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.integration
async def test_list_all_sessions(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/sessions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert all("id" in item for item in body["items"])


@pytest.mark.integration
async def test_list_filtered_by_status(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/sessions", params={"status": "running"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {item["status"] for item in body["items"]} == {"running"}


@pytest.mark.integration
async def test_get_session_by_id(seeded):
    target = seeded["docs"][2]
    target_id = str(target["_id"])

    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/sessions/{target_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == target_id
    assert body["status"] == "completed"
    assert body["display_name"] == "gamma"


@pytest.mark.integration
async def test_get_session_not_found(seeded):
    missing_id = str(ObjectId())

    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/sessions/{missing_id}")

    assert resp.status_code == 404


@pytest.mark.integration
async def test_get_session_invalid_id(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/sessions/not-a-valid-objectid")

    assert resp.status_code == 404
