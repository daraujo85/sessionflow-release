"""Integration tests for GET /directories (TMUX-08).

Runs on the host against the docker stack. The Mongo user can only access the
``sessionflow`` database, so we do NOT create a test DB. Instead we seed an
isolated collection ``host_directories_test_<uuid>`` inside ``sessionflow``
(injected via the ``host_directories_collection`` setting) and drop it on
teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
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
        host_directories_collection=collection_name,
    )


def _doc(path: str, name: str, parent: str, root: str, scanned_at: datetime) -> dict:
    return {
        "path": path,
        "parent": parent,
        "name": name,
        "root": root,
        "scanned_at": scanned_at,
    }


@pytest_asyncio.fixture
async def seeded():
    """Seed an isolated collection with a few directories; drop it on teardown."""
    collection_name = f"host_directories_test_{uuid.uuid4().hex}"
    settings = _host_settings(collection_name)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][collection_name]

    now = datetime.now(UTC)
    docs = [
        _doc("~/dev/portal", "portal", "~/dev", "~", now),
        _doc("~/dev/portal-admin", "portal-admin", "~/dev", "~", now - timedelta(minutes=1)),
        _doc("~/dev/api", "api", "~/dev", "~", now - timedelta(minutes=2)),
        _doc("~/work/billing", "billing", "~/work", "~", now - timedelta(minutes=3)),
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
async def test_query_filters_by_substring(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/directories", params={"q": "portal"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_match"] is False
    paths = {item["path"] for item in body["items"]}
    assert paths == {"~/dev/portal", "~/dev/portal-admin"}
    # Envelope shape: only path/parent/name/root.
    for item in body["items"]:
        assert set(item.keys()) == {"path", "parent", "name", "root"}


@pytest.mark.integration
async def test_query_is_case_insensitive(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/directories", params={"q": "BILL"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_match"] is False
    assert [item["path"] for item in body["items"]] == ["~/work/billing"]


@pytest.mark.integration
async def test_empty_query_returns_recent_up_to_limit(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/directories", params={"limit": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_match"] is False
    assert len(body["items"]) == 2
    # Ordered by scanned_at desc -> two most recent.
    assert [item["path"] for item in body["items"]] == ["~/dev/portal", "~/dev/portal-admin"]


@pytest.mark.integration
async def test_no_match_returns_empty_items(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/directories", params={"q": "nonexistent-xyz"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["no_match"] is True


@pytest.mark.integration
async def test_limit_is_respected(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/directories", params={"q": "dev", "limit": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["no_match"] is False
