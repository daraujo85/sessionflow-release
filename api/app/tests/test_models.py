"""Integration tests for GET /models (MODEL-01).

Runs on the host against the docker stack. The Mongo user can only access the
``sessionflow`` database, so we do NOT create a test DB. Instead we seed an
isolated collection ``host_models_test_<uuid>`` inside ``sessionflow`` (injected
via the ``models_collection`` setting) and drop it on teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

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
        models_collection=collection_name,
    )


@pytest_asyncio.fixture
async def seeded():
    """Seed an isolated host_models collection; drop it on teardown."""
    collection_name = f"host_models_test_{uuid.uuid4().hex}"
    settings = _host_settings(collection_name)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][collection_name]

    now = datetime.now(UTC)
    docs = [
        {
            "agent": "claude",
            "models": [
                {"id": "Opus", "label": "Opus", "description": "Opus 4.8", "is_default": True},
                {
                    "id": "Sonnet",
                    "label": "Sonnet",
                    "description": "Sonnet 4.6",
                    "is_default": False,
                },
            ],
            "source": "picker",
            "scanned_at": now,
        },
        {
            "agent": "opencode",
            "models": [
                {
                    "id": "ollama/qwen2.5-coder:latest",
                    "label": "Qwen 2.5 Coder 7B",
                    "description": None,
                    "is_default": True,
                }
            ],
            "source": "config",
            "scanned_at": now,
        },
        {"agent": "gemini", "models": [], "source": "fallback", "scanned_at": now},
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
async def test_list_all_agents(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/models")

    assert resp.status_code == 200
    body = resp.json()
    agents = {item["agent"] for item in body["items"]}
    assert agents == {"claude", "opencode", "gemini"}
    assert len(body["items"]) >= 2


@pytest.mark.integration
async def test_filter_by_agent(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/models", params={"agent": "claude"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["agent"] == "claude"
    assert item["source"] == "picker"
    labels = {m["label"] for m in item["models"]}
    assert {"Opus", "Sonnet"} <= labels
    default = next(m for m in item["models"] if m["is_default"])
    assert default["id"] == "Opus"


@pytest.mark.integration
async def test_unknown_agent_returns_empty(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/models", params={"agent": "nope"})

    assert resp.status_code == 200
    assert resp.json()["items"] == []
