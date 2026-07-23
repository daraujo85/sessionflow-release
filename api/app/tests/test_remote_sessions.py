"""Integration tests for bookmarks de sessão de OUTRAS contas (ver
``routers/remote_sessions.py``): create, list, delete, e validação da URL.

Runs on the host against the docker stack (Mongo healthy).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import Settings
from app.main import create_app

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


@pytest_asyncio.fixture
async def settings():
    remote_sessions_collection = f"remote_sessions_test_{uuid.uuid4().hex}"

    s = Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        remote_sessions_collection=remote_sessions_collection,
        auth_email="",
        auth_password="",
    )

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][remote_sessions_collection].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.integration
async def test_create_list_delete_remote_session(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            create_resp = await client.post(
                "/remote-sessions",
                json={"label": "Lucas", "url": "https://sessionflow.lucas.dev/s/abc123?k=tok"},
            )
            assert create_resp.status_code == 201
            created = create_resp.json()
            assert created["label"] == "Lucas"
            assert created["url"] == "https://sessionflow.lucas.dev/s/abc123?k=tok"

            list_resp = await client.get("/remote-sessions")
            assert list_resp.status_code == 200
            body = list_resp.json()
            assert body["total"] == 1
            assert body["items"][0]["id"] == created["id"]

            get_resp = await client.get(f"/remote-sessions/{created['id']}")
            assert get_resp.status_code == 200
            assert get_resp.json()["label"] == "Lucas"

            del_resp = await client.delete(f"/remote-sessions/{created['id']}")
            assert del_resp.status_code == 204

            missing_resp = await client.delete(f"/remote-sessions/{created['id']}")
            assert missing_resp.status_code == 404

            list_resp2 = await client.get("/remote-sessions")
    assert list_resp2.json()["total"] == 0


@pytest.mark.integration
async def test_create_rejects_non_http_url(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                "/remote-sessions",
                json={"label": "Everton", "url": "javascript:alert(1)"},
            )
    assert resp.status_code == 422


@pytest.mark.integration
async def test_delete_unknown_not_found(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.delete(f"/remote-sessions/{ObjectId()}")
    assert resp.status_code == 404


@pytest.mark.integration
async def test_get_unknown_not_found(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/remote-sessions/{ObjectId()}")
    assert resp.status_code == 404
