"""Catálogo e distribuição de skills — contrato em
``docs/skills-distribution-contract.md`` (spec: ``docs/specs/skills-google-drive-mvp.md``).

O scanner do Mac publica (``POST /skills/publish``); qualquer host lista, resolve
a versão fixada da sessão (``POST /sessions/{id}/skills/resolve``) e baixa o
pacote em streaming. Os bytes vivem no ``SkillStorage`` (Google Drive em
produção); aqui só passam — o upload do multipart é o único temporário, e o
Starlette o descarta ao fim do request.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.repositories.sessions_repo import SessionsRepository
from app.repositories.skills_repo import SkillsRepository, SkillVersionsRepository
from app.skill_storage import (
    CHUNK_BYTES,
    SkillStorage,
    SkillStorageError,
    SkillStorageNotFound,
)
from app.timeutil import utc_aware_fields

router = APIRouter(tags=["skills"])

# Contrato: pacote total ≤ 100 MiB.
MAX_PACKAGE_BYTES = 100 * 1024 * 1024

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SCOPE = re.compile(r"^(global|project:[A-Za-z0-9][A-Za-z0-9._-]*)$")

class SkillPublishMeta(BaseModel):
    skill_id: str
    name: str
    scope: str
    description: str = ""
    content_hash: str
    package_sha256: str
    size: int = Field(ge=0, le=MAX_PACKAGE_BYTES)
    manifest: dict[str, Any] | None = None
    origin_host: str | None = None
    origin_path: str | None = None

    @model_validator(mode="after")
    def _check(self) -> SkillPublishMeta:
        if not _NAME.match(self.name):
            raise ValueError("name inválido")
        if not _SCOPE.match(self.scope):
            raise ValueError("scope deve ser 'global' ou 'project:<repo>'")
        if self.skill_id != f"{self.scope}:{self.name}":
            raise ValueError("skill_id deve ser '<scope>:<name>'")
        self.content_hash = self.content_hash.lower()
        self.package_sha256 = self.package_sha256.lower()
        if not _HEX64.match(self.content_hash) or not _HEX64.match(self.package_sha256):
            raise ValueError("content_hash/package_sha256 devem ser sha256 hex")
        return self

    @property
    def version_id(self) -> str:
        return self.content_hash[:16]

class SkillVersionOut(BaseModel):
    version_id: str
    content_hash: str
    package_sha256: str
    size: int
    published_at: datetime | None = None
    manifest: dict[str, Any] | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> SkillVersionOut:
        return cls.model_validate(utc_aware_fields(dict(doc), "published_at"))

class SkillOut(BaseModel):
    skill_id: str
    name: str
    scope: str
    description: str = ""
    latest_version_id: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> SkillOut:
        data = utc_aware_fields(dict(doc), "updated_at")
        data["skill_id"] = data.pop("_id")
        return cls.model_validate(data)

class SkillDetailOut(SkillOut):
    origin_host: str | None = None
    origin_path: str | None = None
    versions: list[SkillVersionOut] = []

class SkillPublishOut(BaseModel):
    skill_id: str
    version_id: str
    content_hash: str
    package_sha256: str
    size: int
    published_at: datetime | None = None
    created: bool

class SkillResolveIn(BaseModel):
    skill_id: str = Field(min_length=1)
    version_id: str | None = None

class SkillResolveOut(BaseModel):
    skill_id: str
    version_id: str
    content_hash: str
    package_sha256: str
    # Diagnóstico: "pinned" (já fixada), "inherited" (pin do pai),
    # "explicit" (versão pedida) ou "latest".
    source: str

def _skills(request: Request) -> SkillsRepository:
    s = request.app.state.settings
    return SkillsRepository(request.app.state.mongo_db, s.skills_collection)

def _versions(request: Request) -> SkillVersionsRepository:
    s = request.app.state.settings
    return SkillVersionsRepository(request.app.state.mongo_db, s.skill_versions_collection)

def _storage(request: Request) -> SkillStorage:
    return request.app.state.skill_storage

def pin_key(skill_id: str) -> str:
    """Chave em ``sessions.skill_pins``: o skill_id com ``.``/``$``/``%``
    escapados (Mongo não aceita ``.`` em nome de campo num path ``$set``). O
    valor do pin também carrega o ``skill_id`` cru, pra quem só itera."""
    return skill_id.replace("%", "%25").replace(".", "%2E").replace("$", "%24")

async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(CHUNK_BYTES):
        yield chunk

@router.get("/skills", response_model=list[SkillOut])
async def list_skills(request: Request, q: str | None = None) -> list[SkillOut]:
    return [SkillOut.from_doc(d) for d in await _skills(request).list(q)]

@router.get("/skills/{skill_id}", response_model=SkillDetailOut)
async def get_skill(request: Request, skill_id: str) -> SkillDetailOut:
    doc = await _skills(request).get(skill_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    versions = await _versions(request).list_for_skill(skill_id)
    base = SkillOut.from_doc(doc).model_dump()
    return SkillDetailOut(
        **base,
        origin_host=doc.get("origin_host"),
        origin_path=doc.get("origin_path"),
        versions=[SkillVersionOut.from_doc(v) for v in versions],
    )

@router.post("/skills/publish", response_model=SkillPublishOut)
async def publish_skill(
    request: Request, meta: str = Form(...), package: UploadFile = File(...)
) -> JSONResponse:
    try:
        m = SkillPublishMeta.model_validate(json.loads(meta))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"meta inválido: {exc}") from exc

    versions = _versions(request)
    existing = await versions.get(m.skill_id, m.version_id)
    if existing is not None:
        # Idempotente: mesmo content_hash já publicado → 200, sem upload.
        return _publish_response(existing, created=False, status_code=200)

    # 1ª passada: confere sha256/tamanho ANTES de mandar qualquer byte pro storage.
    digest = hashlib.sha256()
    size = 0
    async for chunk in _upload_chunks(package):
        size += len(chunk)
        if size > MAX_PACKAGE_BYTES:
            raise HTTPException(status_code=413, detail="pacote maior que 100 MiB")
        digest.update(chunk)
    if digest.hexdigest() != m.package_sha256:
        raise HTTPException(status_code=422, detail="sha256 do upload ≠ package_sha256")
    if size != m.size:
        raise HTTPException(status_code=422, detail="tamanho do upload ≠ size")

    await package.seek(0)
    storage = _storage(request)
    filename = f"{m.skill_id.replace(':', '__')}@{m.version_id}.tar.gz"
    try:
        file_id = await storage.put(filename, _upload_chunks(package), size)
    except SkillStorageError as exc:
        raise HTTPException(status_code=502, detail=f"storage: {exc}") from exc

    # Versão só entra no catálogo depois do upload confirmado.
    published_at = datetime.now(UTC)
    doc = {
        "skill_id": m.skill_id,
        "version_id": m.version_id,
        "content_hash": m.content_hash,
        "package_sha256": m.package_sha256,
        "size": size,
        "manifest": m.manifest,
        "drive_file_id": file_id,
        "origin_host": m.origin_host,
        "origin_path": m.origin_path,
        "published_at": published_at,
    }
    if not await versions.create(doc):
        # Publicação concorrente do mesmo hash ganhou: descarta o nosso arquivo.
        try:
            await storage.delete(file_id)
        except SkillStorageError:
            pass
        winner = await versions.get(m.skill_id, m.version_id)
        return _publish_response(winner or doc, created=False, status_code=200)

    await _skills(request).upsert_published(m.model_dump(), m.version_id, published_at)
    return _publish_response(doc, created=True, status_code=201)

def _publish_response(doc: dict[str, Any], *, created: bool, status_code: int) -> JSONResponse:
    out = SkillPublishOut.model_validate(
        {**utc_aware_fields(dict(doc), "published_at"), "created": created}
    )
    return JSONResponse(status_code=status_code, content=out.model_dump(mode="json"))

@router.get("/skills/{skill_id}/versions/{version_id}/package")
async def download_package(request: Request, skill_id: str, version_id: str):
    doc = await _versions(request).get(skill_id, version_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Skill version not found")
    stream = _storage(request).open(doc["drive_file_id"])
    # Puxa o 1º chunk antes de responder: erro do storage vira 404/502 em vez
    # de um 200 truncado.
    try:
        first = await anext(stream, b"")
    except SkillStorageNotFound as exc:
        raise HTTPException(status_code=404, detail="pacote ausente no storage") from exc
    except SkillStorageError as exc:
        raise HTTPException(status_code=502, detail=f"storage: {exc}") from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            if first:
                yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        body(),
        media_type="application/gzip",
        headers={
            "X-Package-Sha256": doc["package_sha256"],
            "Content-Length": str(doc["size"]),
            "Content-Disposition": f'attachment; filename="{version_id}.tar.gz"',
        },
    )

def _check_pin(pin: dict[str, Any], requested: str | None, source: str) -> SkillResolveOut:
    if requested and requested != pin["version_id"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"skill já fixada na versão {pin['version_id']} "
                f"({'herdada do pai' if source == 'inherited' else 'nesta sessão'})"
            ),
        )
    return SkillResolveOut(
        skill_id=pin["skill_id"],
        version_id=pin["version_id"],
        content_hash=pin["content_hash"],
        package_sha256=pin["package_sha256"],
        source=source,
    )

async def _parent_pin(
    request: Request, session: dict[str, Any], key: str
) -> dict[str, Any] | None:
    """Pin da sessão pai (``parent`` = tmux_name de quem delegou). Pega a
    sessão pai mais recente com esse nome — no mesmo host quando houver."""
    parent = session.get("parent")
    if not parent:
        return None
    s = request.app.state.settings
    coll = request.app.state.mongo_db[s.sessions_collection]
    query: dict[str, Any] = {"tmux_name": parent, "_id": {"$ne": session["_id"]}}
    cursor = coll.find(query, projection={"skill_pins": 1, "host_id": 1}).sort(
        "created_at", -1
    )
    candidates = [d async for d in cursor]
    same_host = [d for d in candidates if d.get("host_id") == session.get("host_id")]
    for doc in same_host or candidates:
        pin = (doc.get("skill_pins") or {}).get(key)
        if pin:
            return pin
    return None

@router.post("/sessions/{session_id}/skills/resolve", response_model=SkillResolveOut)
async def resolve_skill(
    request: Request, session_id: str, body: SkillResolveIn
) -> SkillResolveOut:
    s = request.app.state.settings
    sessions = SessionsRepository(request.app.state.mongo_db, s.sessions_collection)
    session = await sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    skill_id = body.skill_id
    requested = (body.version_id or "").strip() or None
    key = pin_key(skill_id)

    current = (session.get("skill_pins") or {}).get(key)
    if current:
        return _check_pin(current, requested, "pinned")

    if await _skills(request).get(skill_id) is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    versions = _versions(request)
    inherited = await _parent_pin(request, session, key)
    if inherited:
        _check_pin(inherited, requested, "inherited")
        target, source = inherited, "inherited"
    elif requested:
        doc = await versions.get(skill_id, requested)
        if doc is None:
            raise HTTPException(status_code=404, detail="Skill version not found")
        target, source = doc, "explicit"
    else:
        doc = await versions.latest(skill_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Skill sem versão publicada")
        target, source = doc, "latest"

    pin = {
        "skill_id": skill_id,
        "version_id": target["version_id"],
        "content_hash": target["content_hash"],
        "package_sha256": target["package_sha256"],
        "source": source,
        "pinned_at": datetime.now(UTC),
    }
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError) as exc:  # pragma: no cover — get_session já filtrou
        raise HTTPException(status_code=404, detail="Session not found") from exc
    coll = request.app.state.mongo_db[s.sessions_collection]
    # Pin atômico: só grava se ainda não existe. Concorrente que perdeu a
    # corrida lê o pin vencedor e devolve ele (ou 409 se pediu outra versão).
    res = await coll.update_one(
        {"_id": oid, f"skill_pins.{key}": {"$exists": False}},
        {"$set": {f"skill_pins.{key}": pin}},
    )
    if res.modified_count == 1:
        return _check_pin(pin, None, source)
    fresh = await sessions.get_session(session_id)
    winner = ((fresh or {}).get("skill_pins") or {}).get(key)
    if not winner:
        raise HTTPException(status_code=404, detail="Session not found")
    return _check_pin(winner, requested, "pinned")
