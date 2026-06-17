"""Integration tests for the live screen mirror endpoint.

Covers ``GET /sessions/{id}/screen``: resolves the session ``_id`` to its
``tmux_name`` and returns the upserted ``session_screen`` doc (``{text, at}``).

Runs on the host against the docker stack. The Mongo user can only access the
``sessionflow`` database, so we seed isolated collections ``*_test_<uuid>``
inside ``sessionflow`` (injected via settings) and drop them on teardown.
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


@pytest_asyncio.fixture
async def seeded():
    suffix = uuid.uuid4().hex
    names = {
        "sessions": f"sessions_test_{suffix}",
        "screen": f"session_screen_test_{suffix}",
    }
    settings = Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=names["sessions"],
        screen_collection=names["screen"],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    db = client[settings.mongo_db]

    oid = ObjectId()
    tmux_name = f"tmux-screen-{suffix}"
    now = datetime.now(UTC)

    await db[names["sessions"]].insert_one(
        {"_id": oid, "tmux_name": tmux_name, "status": "running", "created_at": now}
    )
    await db[names["screen"]].insert_one(
        {"tmux_name": tmux_name, "text": "AGENT SCREEN NOW\nline two", "at": now}
    )

    try:
        yield {"settings": settings, "session_id": str(oid)}
    finally:
        for name in names.values():
            await db[name].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.integration
async def test_screen_returns_text(seeded):
    session_id = seeded["session_id"]
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/sessions/{session_id}/screen")

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "AGENT SCREEN NOW\nline two"
    assert body["at"] is not None


@pytest.mark.integration
async def test_screen_empty_when_no_doc(seeded):
    # Valid ObjectId with no matching session/screen doc -> empty text, null at.
    app = create_app(settings=seeded["settings"])
    missing = str(ObjectId())
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/sessions/{missing}/screen")

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == ""
    assert body["at"] is None
