"""Integration tests for a fila de demandas (POST/GET/PATCH).

Segue o mesmo padrão de `test_schedules.py`: coleções Mongo isoladas por
teste, sem publicar nada no RabbitMQ (fila de demandas é CRUD puro).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
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
        "mongodb://sessionflow:sessionflow"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow"
    )


def _rabbit_host_uri() -> str:
    return _env_value("RABBITMQ_URI_HOST") or "amqp://guest:guest@127.0.0.1:5672/"


def _host_settings(sessions_collection: str, demands_collection: str) -> Settings:
    return Settings(
        mongo_uri_host=_mongo_host_uri(),
        rabbitmq_uri_host=_rabbit_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_collection,
        demands_collection=demands_collection,
        auth_email="",
        auth_password="",
    )


@pytest_asyncio.fixture
async def settings():
    sessions_collection = f"sessions_test_{uuid.uuid4().hex}"
    demands_collection = f"demands_test_{uuid.uuid4().hex}"
    s = _host_settings(sessions_collection, demands_collection)

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][sessions_collection].drop()
        await client[s.mongo_db][demands_collection].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _seed_session_doc(name: str) -> dict:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": name,
        "display_name": name,
        "agent_type": "claude",
        "status": "running",
        "work_dir": "/tmp/work",
        "created_at": now,
        "updated_at": now,
    }


async def _seed_session(settings, name: str) -> str:
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    doc = _seed_session_doc(name)
    await collection.insert_one(doc)
    client.close()
    return str(doc["_id"])


@pytest.mark.integration
async def test_create_list_demand(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            create_resp = await client.post(
                f"/sessions/{base_id}/demands", json={"text": "fazer relatório mensal"}
            )
            assert create_resp.status_code == 201
            created = create_resp.json()
            assert created["text"] == "fazer relatório mensal"
            assert created["status"] == "pending"
            assert created["base_session_id"] == base_id
            assert created["target_session_id"] is None

            list_resp = await client.get(f"/sessions/{base_id}/demands")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]


@pytest.mark.integration
async def test_create_unknown_base_session_404(settings):
    missing_id = str(ObjectId())
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{missing_id}/demands", json={"text": "x"}
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_create_rejects_empty_text(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(f"/sessions/{base_id}/demands", json={"text": ""})
    assert resp.status_code == 422


@pytest.mark.integration
async def test_list_filters_by_status(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            await client.post(f"/sessions/{base_id}/demands", json={"text": "a"})
            created_b = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "b"})
            ).json()
            await client.patch(
                f"/demands/{created_b['id']}", json={"status": "in_progress"}
            )

            resp = await client.get(f"/sessions/{base_id}/demands", params={"status": "pending"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["text"] == "a"


@pytest.mark.integration
async def test_patch_updates_progress_fields(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    target_id = await _seed_session(settings, f"filha-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]

            started = (
                await client.patch(
                    f"/demands/{demand_id}",
                    json={
                        "status": "in_progress",
                        "target_session_id": target_id,
                        "branch": "clone/x",
                    },
                )
            ).json()
            assert started["status"] == "in_progress"
            assert started["target_session_id"] == target_id
            assert started["branch"] == "clone/x"
            assert started["text"] == "x"  # não mexeu

            done = (
                await client.patch(
                    f"/demands/{demand_id}",
                    json={"status": "completed", "summary": "fez X e Y"},
                )
            ).json()
            assert done["status"] == "completed"
            assert done["summary"] == "fez X e Y"
            assert done["target_session_id"] == target_id  # preservado


@pytest.mark.integration
async def test_patch_unknown_id_404(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.patch(
                f"/demands/{ObjectId()}", json={"status": "error"}
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_patch_rejects_invalid_status(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            resp = await client.patch(
                f"/demands/{created['id']}", json={"status": "nao-existe"}
            )
    assert resp.status_code == 422


@pytest.mark.integration
async def test_patch_rejects_invalid_transition(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]

            # pending -> completed direto não é permitido (falta in_progress)
            resp = await client.patch(
                f"/demands/{demand_id}", json={"status": "completed"}
            )
    assert resp.status_code == 409


@pytest.mark.integration
async def test_patch_second_claim_of_in_progress_demand_409(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]

            first = await client.patch(
                f"/demands/{demand_id}", json={"status": "in_progress"}
            )
            assert first.status_code == 200

            # segunda tentativa de "pegar" a mesma demanda já em in_progress
            second = await client.patch(
                f"/demands/{demand_id}", json={"status": "in_progress"}
            )
    assert second.status_code == 409


@pytest.mark.integration
async def test_patch_edits_text_while_pending(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            resp = await client.patch(
                f"/demands/{created['id']}", json={"text": "  x corrigido  "}
            )
    assert resp.status_code == 200
    assert resp.json()["text"] == "x corrigido"


@pytest.mark.integration
async def test_patch_rejects_empty_text(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            resp = await client.patch(f"/demands/{created['id']}", json={"text": "   "})
    assert resp.status_code == 422


@pytest.mark.integration
async def test_patch_rejects_text_edit_once_in_progress(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]
            await client.patch(f"/demands/{demand_id}", json={"status": "in_progress"})

            resp = await client.patch(f"/demands/{demand_id}", json={"text": "y"})
    assert resp.status_code == 409


@pytest.mark.integration
async def test_delete_removes_pending_demand(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]

            resp = await client.delete(f"/demands/{demand_id}")
            assert resp.status_code == 204

            list_resp = await client.get(f"/sessions/{base_id}/demands")
    assert list_resp.json()["total"] == 0


@pytest.mark.integration
async def test_delete_unknown_id_404(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.delete(f"/demands/{ObjectId()}")
    assert resp.status_code == 404


@pytest.mark.integration
async def test_delete_rejects_in_progress_demand(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]
            await client.patch(f"/demands/{demand_id}", json={"status": "in_progress"})

            resp = await client.delete(f"/demands/{demand_id}")
    assert resp.status_code == 409


@pytest.mark.integration
async def test_patch_unknown_target_session_id_404(settings):
    base_id = await _seed_session(settings, f"base-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(f"/sessions/{base_id}/demands", json={"text": "x"})
            ).json()
            demand_id = created["id"]

            resp = await client.patch(
                f"/demands/{demand_id}",
                json={"target_session_id": str(ObjectId())},
            )
    assert resp.status_code == 404
