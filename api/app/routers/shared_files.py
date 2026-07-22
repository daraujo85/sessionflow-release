"""Arquivos que o AGENTE compartilha de volta com o usuário — sentido
INVERSO do upload manual (`POST /sessions/{id}/file`, usuário → sessão).

Fluxo: dentro da própria sessão, o agente roda `tools/sf share <caminho>`
(CLI no host, lê o arquivo do disco e faz o POST aqui). O app então lista e
serve esses arquivos pra download — inclusive fora do Mac (celular), que é
o caso de uso: uma imagem/PDF gerado no Desktop que o usuário não consegue
abrir remotamente de outro jeito.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.repositories.sessions_repo import SessionsRepository
from app.repositories.shared_files_repo import SharedFilesRepository
from app.timeutil import utc_aware_fields

router = APIRouter(tags=["shared-files"])

# Teto generoso mas finito — evita encher o disco do host por engano (loop
# de agente, vídeo grande etc.). Ajustável se um caso legítimo precisar mais.
_MAX_FILE_BYTES = 200 * 1024 * 1024


class SharedFileOut(BaseModel):
    id: str
    session_id: str
    filename: str
    content_type: str
    size: int
    created_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> SharedFileOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        data.pop("stored_path", None)
        data = utc_aware_fields(data, "created_at")
        return cls.model_validate(data)


class SharedFileListOut(BaseModel):
    items: list[SharedFileOut]
    total: int


def _repo(request: Request) -> SharedFilesRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SharedFilesRepository(db, settings.shared_files_collection)


def _sessions_repo(request: Request) -> SessionsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SessionsRepository(db, settings.sessions_collection)


@router.post(
    "/sessions/{session_id}/shared-files",
    response_model=SharedFileOut,
    status_code=201,
)
async def create_shared_file(
    request: Request, session_id: str, file: UploadFile = File(...)
) -> SharedFileOut:
    if await _sessions_repo(request).get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    settings = request.app.state.settings
    original = Path(file.filename or "").name or "arquivo"
    ext = Path(original).suffix.lstrip(".") or "bin"

    target_dir = Path(settings.uploads_dir) / session_id / "shared"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{uuid4().hex}.{ext}"

    body = await file.read()
    if len(body) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"arquivo maior que o limite ({_MAX_FILE_BYTES // (1024 * 1024)}MB)",
        )
    target_path.write_bytes(body)

    doc = await _repo(request).create(
        session_id=session_id,
        filename=original,
        stored_path=str(target_path),
        content_type=file.content_type or "application/octet-stream",
        size=len(body),
    )
    return SharedFileOut.from_doc(doc)


@router.get("/sessions/{session_id}/shared-files", response_model=SharedFileListOut)
async def list_shared_files(request: Request, session_id: str) -> SharedFileListOut:
    docs = await _repo(request).list_for_session(session_id)
    items = [SharedFileOut.from_doc(d) for d in docs]
    return SharedFileListOut(items=items, total=len(items))


@router.get("/shared-files/{file_id}/download")
async def download_shared_file(request: Request, file_id: str) -> FileResponse:
    doc = await _repo(request).get(file_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(doc["stored_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer on disk")
    filename = doc.get("filename") or path.name
    return FileResponse(
        path,
        media_type=doc.get("content_type") or "application/octet-stream",
        # `inline` (não `attachment`, o default do FileResponse com `filename`):
        # o caso de uso é VER a imagem/PDF direto no navegador/celular, sem
        # precisar baixar primeiro pra depois abrir.
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.delete("/shared-files/{file_id}", status_code=204)
async def delete_shared_file(request: Request, file_id: str) -> None:
    doc = await _repo(request).delete(file_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(doc["stored_path"])
    if path.is_file():
        path.unlink(missing_ok=True)
