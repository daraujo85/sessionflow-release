"""Unit tests for DemandsRepository (sem rede — usa mongomock via motor real
seria overkill aqui; testamos contra o Mongo real do host, mesma convenção
dos outros repositórios do projeto — ver test_schedules.py)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.repositories.demands_repo import DemandsRepository

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


@pytest_asyncio.fixture
async def repo():
    collection_name = f"demands_test_{uuid.uuid4().hex}"
    client = AsyncIOMotorClient(_mongo_host_uri())
    db = client["sessionflow"]
    try:
        yield DemandsRepository(db, collection_name)
    finally:
        await db[collection_name].drop()
        client.close()


@pytest.mark.integration
async def test_create_sets_pending_status(repo):
    doc = await repo.create("base-1", "fazer relatório mensal")
    assert doc["status"] == "pending"
    assert doc["base_session_id"] == "base-1"
    assert doc["text"] == "fazer relatório mensal"
    assert doc["target_session_id"] is None
    assert doc["branch"] is None
    assert doc["created_at"] is not None
    assert doc["started_at"] is None
    assert doc["completed_at"] is None
    assert doc["summary"] is None
    assert doc["error_note"] is None


@pytest.mark.integration
async def test_list_for_base_filters_by_session_and_status(repo):
    await repo.create("base-1", "a")
    d2 = await repo.create("base-1", "b")
    await repo.create("base-2", "c")
    await repo.update(str(d2["_id"]), status="in_progress")

    all_base1 = await repo.list_for_base("base-1")
    assert {d["text"] for d in all_base1} == {"a", "b"}

    pending_base1 = await repo.list_for_base("base-1", status="pending")
    assert [d["text"] for d in pending_base1] == ["a"]


@pytest.mark.integration
async def test_list_for_base_orders_most_recent_first(repo):
    await repo.create("base-1", "primeira")
    await repo.create("base-1", "segunda")

    items = await repo.list_for_base("base-1")
    assert [d["text"] for d in items] == ["segunda", "primeira"]


@pytest.mark.integration
async def test_update_only_sets_passed_fields(repo):
    doc = await repo.create("base-1", "x")
    demand_id = str(doc["_id"])

    updated = await repo.update(demand_id, status="in_progress", started_at=None)
    assert updated["status"] == "in_progress"
    assert updated["text"] == "x"  # não mexeu

    updated2 = await repo.update(demand_id, target_session_id="sess-123", branch="clone/x")
    assert updated2["status"] == "in_progress"  # preservado
    assert updated2["target_session_id"] == "sess-123"
    assert updated2["branch"] == "clone/x"


@pytest.mark.integration
async def test_update_with_expected_status_mismatch_returns_none(repo):
    doc = await repo.create("base-1", "x")
    demand_id = str(doc["_id"])

    # status atual é "pending" — pedir update esperando "in_progress" falha
    result = await repo.update(demand_id, expected_status="in_progress", status="completed")
    assert result is None

    # doc não foi alterado
    unchanged = await repo.get(demand_id)
    assert unchanged["status"] == "pending"


@pytest.mark.integration
async def test_update_with_expected_status_match_applies(repo):
    doc = await repo.create("base-1", "x")
    demand_id = str(doc["_id"])

    result = await repo.update(demand_id, expected_status="pending", status="in_progress")
    assert result is not None
    assert result["status"] == "in_progress"


@pytest.mark.integration
async def test_update_unknown_id_returns_none(repo):
    from bson import ObjectId

    assert await repo.update(str(ObjectId()), status="error") is None


@pytest.mark.integration
async def test_get_unknown_id_returns_none(repo):
    from bson import ObjectId

    assert await repo.get(str(ObjectId())) is None


@pytest.mark.integration
async def test_delete_removes_doc(repo):
    doc = await repo.create("base-1", "x")
    demand_id = str(doc["_id"])

    assert await repo.delete(demand_id) is True
    assert await repo.get(demand_id) is None


@pytest.mark.integration
async def test_delete_unknown_id_returns_false(repo):
    from bson import ObjectId

    assert await repo.delete(str(ObjectId())) is False


@pytest.mark.integration
async def test_delete_with_expected_status_mismatch_returns_false(repo):
    doc = await repo.create("base-1", "x")
    demand_id = str(doc["_id"])

    assert await repo.delete(demand_id, expected_status="in_progress") is False
    assert await repo.get(demand_id) is not None  # não apagou
