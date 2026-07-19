"""Integration tests for arquivos compartilhados pelo agente (reverse of the
manual upload flow — see ``tools/sf share`` and ``routers/shared_files.py``).

Covers create (multipart upload), list, download (with/without auth token),
and delete (removing the doc AND the physical file on disk).

Runs on the host against the docker stack (Mongo healthy). Writes real files
under a temp uploads dir (isolated per test, cleaned up on teardown).
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
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
        "mongodb://sessionflow:882938ade9f4298f59daccc2dc5add74"
        "@127.0.0.1:27017/sessionflow?authSource=sessionflow"
    )


@pytest_asyncio.fixture
async def settings():
    sessions_collection = f"sessions_test_{uuid.uuid4().hex}"
    shared_files_collection = f"shared_files_test_{uuid.uuid4().hex}"
    uploads_dir = tempfile.mkdtemp(prefix="sf_shared_files_test_")

    s = Settings(
        mongo_uri_host=_mongo_host_uri(),
        use_host_uris=True,
        mongo_db="sessionflow",
        sessions_collection=sessions_collection,
        shared_files_collection=shared_files_collection,
        uploads_dir=uploads_dir,
        # Ver test_schedules.py: neutraliza um shell dev com
        # SESSIONFLOW_EMAIL/PASSWORD exportado no ambiente.
        auth_email="",
        auth_password="",
    )

    client = AsyncIOMotorClient(s.effective_mongo_uri)
    try:
        yield s
    finally:
        await client[s.mongo_db][sessions_collection].drop()
        await client[s.mongo_db][shared_files_collection].drop()
        client.close()
        shutil.rmtree(uploads_dir, ignore_errors=True)


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
    client = AsyncIOMotorClient(settings.effective_mongo_uri)
    collection = client[settings.mongo_db][settings.sessions_collection]
    doc = _seed_doc(status, name)
    await collection.insert_one(doc)
    client.close()
    return str(doc["_id"])


@pytest.mark.integration
async def test_create_list_download_shared_file(settings):
    session_id = await _seed(settings, "running", f"sf-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            content = b"conteudo de teste"
            create_resp = await client.post(
                f"/sessions/{session_id}/shared-files",
                files={"file": ("relatorio.txt", io.BytesIO(content), "text/plain")},
            )
            assert create_resp.status_code == 201
            created = create_resp.json()
            assert created["filename"] == "relatorio.txt"
            assert created["content_type"] == "text/plain"
            assert created["size"] == len(content)
            assert created["session_id"] == session_id
            assert "stored_path" not in created  # não vaza path do host

            list_resp = await client.get(f"/sessions/{session_id}/shared-files")
            assert list_resp.status_code == 200
            body = list_resp.json()
            assert body["total"] == 1
            assert body["items"][0]["id"] == created["id"]

            download_resp = await client.get(f"/shared-files/{created['id']}/download")
    assert download_resp.status_code == 200
    assert download_resp.content == content
    assert download_resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert 'inline; filename="relatorio.txt"' == download_resp.headers["content-disposition"]


@pytest.mark.integration
async def test_create_unknown_session_not_found(settings):
    missing_id = str(ObjectId())
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.post(
                f"/sessions/{missing_id}/shared-files",
                files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
            )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_download_missing_not_found(settings):
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            resp = await client.get(f"/shared-files/{ObjectId()}/download")
    assert resp.status_code == 404


@pytest.mark.integration
async def test_delete_removes_doc_and_file(settings):
    session_id = await _seed(settings, "running", f"sf-{uuid.uuid4().hex[:8]}")
    app = create_app(settings=settings)
    async with await _client(app) as client:
        async with app.router.lifespan_context(app):
            created = (
                await client.post(
                    f"/sessions/{session_id}/shared-files",
                    files={"file": ("a.png", io.BytesIO(b"\x89PNG"), "image/png")},
                )
            ).json()

            # Confirma que o arquivo físico existe antes de excluir.
            files_before = list(Path(settings.uploads_dir).rglob("*.png"))
            assert len(files_before) == 1

            del_resp = await client.delete(f"/shared-files/{created['id']}")
            assert del_resp.status_code == 204

            missing_resp = await client.delete(f"/shared-files/{created['id']}")
            assert missing_resp.status_code == 404

            list_resp = await client.get(f"/sessions/{session_id}/shared-files")
    assert list_resp.json()["total"] == 0
    files_after = list(Path(settings.uploads_dir).rglob("*.png"))
    assert files_after == []
