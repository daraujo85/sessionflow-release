"""Integration tests for the D5 read endpoints (DASH-03/09/10).

Covers:
* ``GET /sessions/{id}/output`` (with ``after`` filter)
* ``GET /events/history`` (with ``day`` filter)
* ``GET /notifications`` (kind filtering)
* ``GET /tasks`` (session filtering)

Runs on the host against the docker stack. The Mongo user can only access the
``sessionflow`` database, so we do NOT create a test DB. Instead we seed
isolated collections ``*_test_<uuid>`` inside ``sessionflow`` (injected via the
matching settings) and drop them on teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

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


def _host_settings(suffix: str) -> tuple[Settings, dict[str, str]]:
    names = {
        "output": f"session_output_test_{suffix}",
        "events": f"events_test_{suffix}",
        "tasks": f"tasks_test_{suffix}",
    }
    settings = Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        output_collection=names["output"],
        events_collection=names["events"],
        tasks_collection=names["tasks"],
        notifications_collection=names["events"],
    )
    return settings, names


@pytest_asyncio.fixture
async def seeded():
    """Seed isolated output/events/tasks collections; drop them on teardown."""
    suffix = uuid.uuid4().hex
    settings, names = _host_settings(suffix)

    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    db = client[settings.mongo_db]

    session_id = str(ObjectId())
    other_session = str(ObjectId())
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    output_docs = [
        {
            "session_id": session_id,
            "tmux_name": "tmux-alpha",
            "seq": seq,
            "text": f"line {seq}",
            "line_type": "out",
            "at": now,
        }
        for seq in (1, 2, 3, 4)
    ]
    await db[names["output"]].insert_many(output_docs)

    event_docs = [
        {
            "session_id": session_id,
            "type": "agent",
            "kind": "info",
            "title": "today-info",
            "desc": "d",
            "at": now,
            "seq": 1,
        },
        {
            "session_id": session_id,
            "type": "agent",
            "kind": "attention",
            "title": "today-attention",
            "desc": "d",
            "at": now,
            "seq": 2,
        },
        {
            "session_id": session_id,
            "type": "agent",
            "kind": "success",
            "title": "yesterday-success",
            "desc": "d",
            "at": yesterday,
            "seq": 3,
        },
        {
            "session_id": session_id,
            "type": "agent",
            "kind": "debug",  # not a notification kind
            "title": "today-debug",
            "desc": "d",
            "at": now,
            "seq": 4,
        },
    ]
    await db[names["events"]].insert_many(event_docs)

    task_docs = [
        {"session_id": session_id, "title": "t1", "state": "todo", "updated_at": now},
        {"session_id": session_id, "title": "t2", "state": "doing", "updated_at": now},
        {
            "session_id": other_session,
            "title": "t3",
            "state": "done",
            "updated_at": now,
        },
    ]
    await db[names["tasks"]].insert_many(task_docs)

    try:
        yield {
            "settings": settings,
            "session_id": session_id,
            "other_session": other_session,
            "now": now,
            "yesterday": yesterday,
        }
    finally:
        for name in names.values():
            await db[name].drop()
        client.close()


async def _client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.integration
async def test_output_after_filter_and_order(seeded):
    session_id = seeded["session_id"]
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(
                f"/sessions/{session_id}/output", params={"after": 2}
            )

    assert resp.status_code == 200
    body = resp.json()
    seqs = [item["seq"] for item in body["items"]]
    assert seqs == [3, 4]  # only seq > 2, ascending
    assert body["total"] == 2
    assert all("id" in item for item in body["items"])


@pytest.mark.integration
async def test_output_limit(seeded):
    session_id = seeded["session_id"]
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(
                f"/sessions/{session_id}/output", params={"limit": 2}
            )

    assert resp.status_code == 200
    body = resp.json()
    assert [item["seq"] for item in body["items"]] == [1, 2]


@pytest.mark.integration
async def test_events_history_day_filter(seeded):
    day = seeded["now"].strftime("%Y-%m-%d")
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/events/history", params={"day": day})

    assert resp.status_code == 200
    body = resp.json()
    titles = {item["title"] for item in body["items"]}
    # Only today's events (the yesterday-success one is excluded).
    assert "yesterday-success" not in titles
    assert {"today-info", "today-attention", "today-debug"} <= titles


@pytest.mark.integration
async def test_events_history_all(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/events/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    # Ordered by at descending: yesterday-success must be last.
    assert body["items"][-1]["title"] == "yesterday-success"


@pytest.mark.integration
async def test_notifications_kind_filter(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/notifications")

    assert resp.status_code == 200
    body = resp.json()
    kinds = {item["kind"] for item in body["items"]}
    # 'debug' is filtered out; only known notification kinds remain.
    assert kinds == {"info", "attention", "success"}
    assert "debug" not in kinds
    for item in body["items"]:
        assert {"title", "desc", "kind", "at", "session_id"} <= set(item.keys())


@pytest.mark.integration
async def test_tasks_session_filter(seeded):
    session_id = seeded["session_id"]
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/tasks", params={"session": session_id})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {item["session_id"] for item in body["items"]} == {session_id}


@pytest.mark.integration
async def test_tasks_all(seeded):
    app = create_app(settings=seeded["settings"])
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/tasks")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
