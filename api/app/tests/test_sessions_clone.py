"""Integration tests for POST /sessions/{id}/clone.

Segue o mesmo padrão de `test_sessions_create.py`: coleção Mongo isolada por
teste, comandos publicados na fila do host de teste (drenados/removidos no
teardown pra nunca chegar no worker real).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.main import create_app
from app.tests.test_sessions_create import (
    _TEST_HOST_ID,
    _client,
    drain_commands,
    settings,
)


def _seed_session(
    *,
    name: str,
    git_repos: list[dict] | None,
    host_id: str = _TEST_HOST_ID,
) -> dict:
    now = datetime.now(UTC)
    return {
        "_id": ObjectId(),
        "tmux_name": name,
        "display_name": name,
        "agent_type": "claude",
        "model": "opus",
        "effort": "high",
        "status": "running",
        "work_dir": "/tmp/repo",
        "git_repos": git_repos,
        "host_id": host_id,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.integration
async def test_clone_eligible_publishes_command(settings, drain_commands):
    name = f"clone-src-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    command_id = body["command_id"]

    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["type"] == "clone"
    assert msg["payload"]["name"] == f"{name}-clone"
    assert msg["payload"]["repo_path"] == "/tmp/repo"
    assert msg["payload"]["source_session_id"] == str(doc["_id"])
    assert msg["payload"]["agent_type"] == "claude"
    assert msg["payload"]["model"] == "opus"
    assert msg["payload"]["effort"] == "high"


@pytest.mark.integration
async def test_clone_dedupes_name_on_collision(settings, drain_commands):
    name = f"clone-dup-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )
    existing_clone = _seed_session(name=f"{name}-clone", git_repos=None)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    coll = client[settings.mongo_db][settings.sessions_collection]
    await coll.insert_one(doc)
    await coll.insert_one(existing_clone)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == f"{name}-clone-2"


@pytest.mark.integration
async def test_clone_dedupes_name_against_stopped_session(settings, drain_commands):
    name = f"clone-stopped-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )
    stopped_clone = _seed_session(name=f"{name}-clone", git_repos=None)
    stopped_clone["status"] = "stopped"

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    coll = client[settings.mongo_db][settings.sessions_collection]
    await coll.insert_one(doc)
    await coll.insert_one(stopped_clone)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == f"{name}-clone-2"


@pytest.mark.integration
async def test_clone_rejects_umbrella_work_dir(settings):
    name = f"clone-umb-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[
            {"name": "backend", "path": "/tmp/repo/backend", "branch": "main"},
            {"name": "frontend", "path": "/tmp/repo/frontend", "branch": "main"},
        ],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone")

    assert resp.status_code == 400


@pytest.mark.integration
async def test_clone_not_found_404(settings):
    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{ObjectId()}/clone")
    assert resp.status_code == 404


@pytest.mark.integration
async def test_clone_uses_name_hint_when_given(settings, drain_commands):
    name = f"clone-hint-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(
                f"/sessions/{doc['_id']}/clone",
                json={"name_hint": "Relatório Mensal!"},
            )

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == "relatorio-mensal"


@pytest.mark.integration
async def test_clone_without_body_still_works(settings, drain_commands):
    """Retrocompat: front hoje manda POST com corpo `{}` (sem name_hint)."""
    name = f"clone-nobody-{uuid.uuid4().hex[:8]}"
    doc = _seed_session(
        name=name,
        git_repos=[{"name": ".", "path": "/tmp/repo", "branch": "main"}],
    )

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    await client[settings.mongo_db][settings.sessions_collection].insert_one(doc)
    client.close()

    app = create_app(settings=settings)
    async with await _client(app) as http:
        async with app.router.lifespan_context(app):
            resp = await http.post(f"/sessions/{doc['_id']}/clone", json={})

    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    msg = await drain_commands(command_id)
    assert msg is not None
    assert msg["payload"]["name"] == f"{name}-clone"
