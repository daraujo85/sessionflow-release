"""Testes do catálogo/distribuição de skills (``routers/skills.py``,
``skill_storage.py``) — contrato em ``docs/skills-distribution-contract.md``.

Unitários: Mongo é o ``mongomock-motor`` (sem docker), storage é o
``LocalStorage`` num tmp dir e o Google Drive é exercitado com
``httpx.MockTransport`` (sem credencial real).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.main import create_app
from app.routers.skills import pin_key
from app.skill_storage import (
    GoogleDriveStorage,
    LocalStorage,
    SkillStorageError,
    SkillStorageNotFound,
    build_skill_storage,
)

SKILL_ID = "global:sf-delegate"

def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        auth_email="",
        auth_password="",
        uploads_dir=str(tmp_path / "uploads"),
        skills_local_dir=str(tmp_path / "skills"),
        google_client_id="",
        google_client_secret="",
        google_refresh_token="",
        skills_drive_folder_id="",
    )
    return Settings(**{**base, **overrides})

@pytest.fixture
def app(tmp_path):
    application = create_app(settings=_settings(tmp_path))
    # Sem lifespan (não conecta Mongo/Rabbit reais): injeta o mock.
    application.state.mongo_db = AsyncMongoMockClient()["sessionflow"]
    return application

@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

def _package(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()

def _content_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(f"{path}\0{hashlib.sha256(files[path]).hexdigest()}\n".encode())
    return h.hexdigest()

def _artifact(text: str = "v1", skill_id: str = SKILL_ID, **meta_overrides):
    files = {"SKILL.md": f"---\ndescription: delega\n---\n{text}\n".encode()}
    pkg = _package(files)
    scope, name = skill_id.rsplit(":", 1)
    meta = {
        "skill_id": skill_id,
        "name": name,
        "scope": scope,
        "description": "delega tarefas",
        "content_hash": _content_hash(files),
        "package_sha256": hashlib.sha256(pkg).hexdigest(),
        "size": len(pkg),
        "manifest": None,
        "origin_host": "mac",
        "origin_path": f"~/.claude/skills/{name}",
        **meta_overrides,
    }
    return meta, pkg

async def _publish(client, meta, pkg):
    return await client.post(
        "/skills/publish",
        data={"meta": json.dumps(meta)},
        files={"package": ("pkg.tar.gz", io.BytesIO(pkg), "application/gzip")},
    )

async def _seed_session(app, **fields) -> str:
    doc = {
        "_id": ObjectId(),
        "tmux_name": f"sf-{uuid.uuid4().hex[:8]}",
        "status": "running",
        "host_id": "deepin",
        "created_at": datetime.now(UTC),
        **fields,
    }
    await app.state.mongo_db[app.state.settings.sessions_collection].insert_one(doc)
    return str(doc["_id"])

def _stored_files(tmp_path) -> list:
    root = tmp_path / "skills"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []

# --- publish ---------------------------------------------------------------

async def test_publish_new_version_201(client, tmp_path):
    meta, pkg = _artifact()
    resp = await _publish(client, meta, pkg)
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] is True
    assert body["version_id"] == meta["content_hash"][:16]
    assert body["package_sha256"] == meta["package_sha256"]
    assert len(_stored_files(tmp_path)) == 1

async def test_publish_same_hash_is_idempotent_200_without_upload(client, tmp_path):
    meta, pkg = _artifact()
    first = await _publish(client, meta, pkg)
    again = await _publish(client, meta, pkg)
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["version_id"] == first.json()["version_id"]
    assert len(_stored_files(tmp_path)) == 1  # sem 2º upload

    detail = (await client.get(f"/skills/{SKILL_ID}")).json()
    assert len(detail["versions"]) == 1

async def test_publish_wrong_sha_422_and_nothing_registered(client, tmp_path):
    meta, pkg = _artifact()
    meta["package_sha256"] = "0" * 64
    resp = await _publish(client, meta, pkg)
    assert resp.status_code == 422
    assert _stored_files(tmp_path) == []
    assert (await client.get(f"/skills/{SKILL_ID}")).status_code == 404

async def test_publish_rejects_inconsistent_skill_id(client):
    meta, pkg = _artifact(skill_id="global:a")
    meta["skill_id"] = "global:b"
    assert (await _publish(client, meta, pkg)).status_code == 422

# --- list / get ------------------------------------------------------------

async def test_list_and_get(client):
    m1, p1 = _artifact("v1")
    m2, p2 = _artifact("outra", skill_id="project:sessionflow:deploy")
    await _publish(client, m1, p1)
    await _publish(client, m2, p2)

    items = (await client.get("/skills")).json()
    assert {i["skill_id"] for i in items} == {SKILL_ID, "project:sessionflow:deploy"}
    deploy = next(i for i in items if i["skill_id"] == "project:sessionflow:deploy")
    assert deploy["scope"] == "project:sessionflow"
    assert deploy["name"] == "deploy"
    assert deploy["latest_version_id"] == m2["content_hash"][:16]

    filtered = (await client.get("/skills", params={"q": "DEPLOY"})).json()
    assert [i["skill_id"] for i in filtered] == ["project:sessionflow:deploy"]

    # skill_id url-encoded na rota.
    resp = await client.get("/skills/global%3Asf-delegate")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["description"] == "delega tarefas"
    assert detail["versions"][0]["content_hash"] == m1["content_hash"]
    assert detail["versions"][0]["size"] == m1["size"]
    assert "drive_file_id" not in detail["versions"][0]

    assert (await client.get("/skills/global%3Anada")).status_code == 404

async def test_new_version_becomes_latest(client):
    m1, p1 = _artifact("v1")
    m2, p2 = _artifact("v2")
    await _publish(client, m1, p1)
    await _publish(client, m2, p2)
    detail = (await client.get(f"/skills/{SKILL_ID}")).json()
    assert detail["latest_version_id"] == m2["content_hash"][:16]
    assert [v["version_id"] for v in detail["versions"]] == [
        m2["content_hash"][:16],
        m1["content_hash"][:16],
    ]

# --- download --------------------------------------------------------------

async def test_download_streams_bytes_with_sha_header(client):
    meta, pkg = _artifact()
    version_id = (await _publish(client, meta, pkg)).json()["version_id"]
    async with client.stream(
        "GET", f"/skills/global%3Asf-delegate/versions/{version_id}/package"
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"
        assert resp.headers["x-package-sha256"] == meta["package_sha256"]
        data = b"".join([chunk async for chunk in resp.aiter_bytes()])
    assert data == pkg
    assert hashlib.sha256(data).hexdigest() == meta["package_sha256"]

async def test_download_unknown_version_404(client):
    meta, pkg = _artifact()
    await _publish(client, meta, pkg)
    resp = await client.get(f"/skills/{SKILL_ID}/versions/deadbeefdeadbeef/package")
    assert resp.status_code == 404

async def test_download_missing_in_storage_404(client, tmp_path):
    meta, pkg = _artifact()
    version_id = (await _publish(client, meta, pkg)).json()["version_id"]
    for f in (tmp_path / "skills").iterdir():
        f.unlink()
    resp = await client.get(f"/skills/{SKILL_ID}/versions/{version_id}/package")
    assert resp.status_code == 404

# --- resolve ---------------------------------------------------------------

async def _resolve(client, session_id, **body):
    return await client.post(
        f"/sessions/{session_id}/skills/resolve", json={"skill_id": SKILL_ID, **body}
    )

async def test_resolve_pins_latest(client, app):
    m1, p1 = _artifact("v1")
    m2, p2 = _artifact("v2")
    await _publish(client, m1, p1)
    await _publish(client, m2, p2)
    sid = await _seed_session(app)

    resp = await _resolve(client, sid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["version_id"] == m2["content_hash"][:16]
    assert body["content_hash"] == m2["content_hash"]
    assert body["package_sha256"] == m2["package_sha256"]
    assert body["source"] == "latest"

    doc = await app.state.mongo_db[app.state.settings.sessions_collection].find_one(
        {"_id": ObjectId(sid)}
    )
    pin = doc["skill_pins"][pin_key(SKILL_ID)]
    assert pin["skill_id"] == SKILL_ID
    assert pin["version_id"] == body["version_id"]

async def test_resolve_repeated_keeps_pin_after_new_publish(client, app):
    m1, p1 = _artifact("v1")
    await _publish(client, m1, p1)
    sid = await _seed_session(app)
    first = (await _resolve(client, sid)).json()

    m2, p2 = _artifact("v2")
    await _publish(client, m2, p2)
    again = await _resolve(client, sid)
    assert again.status_code == 200
    assert again.json()["version_id"] == first["version_id"]
    assert again.json()["source"] == "pinned"

    # Mesma versão explícita também é aceita.
    same = await _resolve(client, sid, version_id=first["version_id"])
    assert same.status_code == 200

    # Sessão nova pega a publicação nova.
    other = await _seed_session(app)
    assert (await _resolve(client, other)).json()["version_id"] == m2["content_hash"][:16]

async def test_resolve_explicit_divergent_version_409(client, app):
    m1, p1 = _artifact("v1")
    m2, p2 = _artifact("v2")
    await _publish(client, m1, p1)
    await _publish(client, m2, p2)
    sid = await _seed_session(app)
    await _resolve(client, sid)  # fixa latest (v2)

    resp = await _resolve(client, sid, version_id=m1["content_hash"][:16])
    assert resp.status_code == 409

async def test_resolve_explicit_version_pins_it(client, app):
    m1, p1 = _artifact("v1")
    m2, p2 = _artifact("v2")
    await _publish(client, m1, p1)
    await _publish(client, m2, p2)
    sid = await _seed_session(app)
    v1 = m1["content_hash"][:16]
    resp = await _resolve(client, sid, version_id=v1)
    assert resp.status_code == 200
    assert resp.json()["version_id"] == v1
    assert resp.json()["source"] == "explicit"
    assert (await _resolve(client, sid)).json()["version_id"] == v1

async def test_resolve_inherits_parent_pin(client, app):
    m1, p1 = _artifact("v1")
    await _publish(client, m1, p1)
    parent_id = await _seed_session(app, tmux_name="sesionflow")
    pinned = (await _resolve(client, parent_id)).json()

    m2, p2 = _artifact("v2")
    await _publish(client, m2, p2)
    child_id = await _seed_session(app, parent="sesionflow")

    resp = await _resolve(client, child_id)
    assert resp.status_code == 200
    assert resp.json()["version_id"] == pinned["version_id"]
    assert resp.json()["source"] == "inherited"
    # Filho também fica fixado (não depende mais do pai).
    assert (await _resolve(client, child_id)).json()["source"] == "pinned"

    # Pedir outra versão que diverge do pin do pai → 409.
    other_child = await _seed_session(app, parent="sesionflow")
    resp = await _resolve(client, other_child, version_id=m2["content_hash"][:16])
    assert resp.status_code == 409

async def test_resolve_parent_without_pin_uses_latest(client, app):
    m1, p1 = _artifact("v1")
    await _publish(client, m1, p1)
    await _seed_session(app, tmux_name="pai")
    child_id = await _seed_session(app, parent="pai")
    resp = await _resolve(client, child_id)
    assert resp.json()["source"] == "latest"

async def test_resolve_concurrent_calls_get_same_version(client, app):
    m1, p1 = _artifact("v1")
    await _publish(client, m1, p1)
    sid = await _seed_session(app)
    results = await asyncio.gather(*[_resolve(client, sid) for _ in range(5)])
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["version_id"] for r in results}) == 1

async def test_resolve_404s(client, app):
    sid = await _seed_session(app)
    assert (await _resolve(client, sid)).status_code == 404  # skill inexistente
    assert (await _resolve(client, str(ObjectId()))).status_code == 404  # sessão
    m1, p1 = _artifact("v1")
    await _publish(client, m1, p1)
    resp = await _resolve(client, sid, version_id="ffffffffffffffff")
    assert resp.status_code == 404

# --- storage ---------------------------------------------------------------

async def _aiter(*chunks: bytes):
    for c in chunks:
        yield c

async def test_local_storage_roundtrip(tmp_path):
    storage = LocalStorage(tmp_path)
    file_id = await storage.put("x.tar.gz", _aiter(b"ab", b"cd"), 4)
    assert b"".join([c async for c in storage.open(file_id)]) == b"abcd"
    await storage.delete(file_id)
    with pytest.raises(SkillStorageNotFound):
        [c async for c in storage.open(file_id)]
    with pytest.raises(SkillStorageNotFound):
        [c async for c in storage.open("../etc/passwd")]

def test_build_storage_selects_by_env(tmp_path):
    assert isinstance(build_skill_storage(_settings(tmp_path)), LocalStorage)
    drive = build_skill_storage(
        _settings(
            tmp_path,
            google_client_id="cid",
            google_client_secret="sec",
            google_refresh_token="rt",
            skills_drive_folder_id="folder123",
        )
    )
    assert isinstance(drive, GoogleDriveStorage)

def test_settings_read_google_env_names(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "rt")
    monkeypatch.setenv("SKILLS_DRIVE_FOLDER_ID", "fid")
    s = Settings()
    assert s.google_drive_configured
    assert s.skills_drive_folder_id == "fid"

class FakeDrive:
    """Handler do ``httpx.MockTransport`` imitando o pedaço do Drive v3 usado."""

    def __init__(self, *, fail_first_token: bool = False) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.tokens = 0
        self.upload_meta: dict | None = None
        self.folder_created = False
        self.expire_first_download = fail_first_token

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append((request.method, url.path))
        if url.host == "oauth2.googleapis.com":
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "refresh_token"
            assert form["refresh_token"] == "rt"
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok{self.tokens}", "expires_in": 3600}
            )

        if not request.headers.get("authorization", "").startswith("Bearer tok"):
            return httpx.Response(401)

        if url.path == "/upload/drive/v3/files" and request.method == "POST":
            assert url.params["uploadType"] == "resumable"
            self.upload_meta = json.loads(request.content)
            return httpx.Response(
                200, headers={"Location": "https://www.googleapis.com/upload/session/abc"}
            )
        if url.path == "/upload/session/abc" and request.method == "PUT":
            file_id = f"drv{len(self.files) + 1}"
            self.files[file_id] = request.read()
            return httpx.Response(200, json={"id": file_id})
        if url.path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": []})
        if url.path == "/drive/v3/files" and request.method == "POST":
            self.folder_created = True
            return httpx.Response(200, json={"id": "newfolder"})
        if url.path.startswith("/drive/v3/files/"):
            file_id = url.path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                self.files.pop(file_id, None)
                return httpx.Response(204)
            assert url.params["alt"] == "media"
            if self.expire_first_download:
                self.expire_first_download = False
                return httpx.Response(401)
            if file_id not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[file_id])
        return httpx.Response(400)

def _drive(fake: FakeDrive, folder_id: str = "folder123") -> GoogleDriveStorage:
    return GoogleDriveStorage(
        "cid", "sec", "rt", folder_id, transport=httpx.MockTransport(fake), backoff_seconds=0
    )

async def test_drive_put_and_open_streaming():
    fake = FakeDrive()
    storage = _drive(fake)
    file_id = await storage.put("global__x@abc.tar.gz", _aiter(b"he", b"llo"), 5)
    assert fake.files[file_id] == b"hello"
    assert fake.upload_meta == {
        "name": "global__x@abc.tar.gz",
        "parents": ["folder123"],
        "mimeType": "application/gzip",
    }
    assert b"".join([c async for c in storage.open(file_id)]) == b"hello"
    assert fake.tokens == 1  # token reaproveitado entre chamadas
    await storage.delete(file_id)
    assert file_id not in fake.files

async def test_drive_refreshes_token_on_401_and_404_maps_not_found():
    fake = FakeDrive(fail_first_token=True)
    storage = _drive(fake)
    file_id = await storage.put("a.tar.gz", _aiter(b"x"), 1)
    assert b"".join([c async for c in storage.open(file_id)]) == b"x"
    assert fake.tokens == 2
    with pytest.raises(SkillStorageNotFound):
        [c async for c in storage.open("nope")]

async def test_drive_creates_folder_when_unset():
    fake = FakeDrive()
    storage = _drive(fake, folder_id="")
    await storage.put("a.tar.gz", _aiter(b"x"), 1)
    assert fake.folder_created
    assert fake.upload_meta["parents"] == ["newfolder"]

async def test_drive_token_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    storage = GoogleDriveStorage(
        "cid", "sec", "rt", "f", transport=httpx.MockTransport(handler), backoff_seconds=0
    )
    with pytest.raises(SkillStorageError):
        await storage.put("a.tar.gz", _aiter(b"x"), 1)

async def test_publish_and_download_through_drive(app, client):
    fake = FakeDrive()
    app.state.skill_storage = _drive(fake)
    meta, pkg = _artifact()
    resp = await _publish(client, meta, pkg)
    assert resp.status_code == 201
    assert list(fake.files.values()) == [pkg]
    dl = await client.get(f"/skills/{SKILL_ID}/versions/{resp.json()['version_id']}/package")
    assert dl.status_code == 200
    assert dl.content == pkg
    assert dl.headers["x-package-sha256"] == meta["package_sha256"]

async def test_publish_storage_failure_502_no_version(app, client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    app.state.skill_storage = GoogleDriveStorage(
        "cid", "sec", "rt", "f", transport=httpx.MockTransport(handler), backoff_seconds=0
    )
    meta, pkg = _artifact()
    assert (await _publish(client, meta, pkg)).status_code == 502
    assert (await client.get(f"/skills/{SKILL_ID}")).status_code == 404
