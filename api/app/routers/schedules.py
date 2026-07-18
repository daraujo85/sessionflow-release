"""Comandos programados: instrução recorrente enviada ao terminal de uma
sessão (ex.: "rode a skill X" a cada 1h). Criar/pausar/retomar/editar/
excluir aqui; a EXECUÇÃO em si roda no loop de ``app.scheduler``, que chama
o mesmo caminho do ``POST /sessions/{id}/input`` manual.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.repositories.schedules_repo import SchedulesRepository
from app.repositories.sessions_repo import SessionsRepository

router = APIRouter(tags=["schedules"])

# Limites sãos: menos de 1 minuto vira spam no terminal; acima de 30 dias não
# faz sentido pra um recorrente (o usuário quer um lembrete pontual, não isso).
_MIN_INTERVAL_SECONDS = 60
_MAX_INTERVAL_SECONDS = 60 * 60 * 24 * 30


class ScheduleOut(BaseModel):
    """Serialized scheduled-command document."""

    id: str
    session_id: str
    text: str
    interval_seconds: int
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> ScheduleOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        return cls.model_validate(data)


class ScheduleListOut(BaseModel):
    items: list[ScheduleOut]
    total: int


class ScheduleCreate(BaseModel):
    text: str = Field(min_length=1)
    interval_seconds: int = Field(ge=_MIN_INTERVAL_SECONDS, le=_MAX_INTERVAL_SECONDS)


class SchedulePatch(BaseModel):
    """Todos os campos opcionais — manda só o que quer mudar (pausar/editar)."""

    text: str | None = Field(default=None, min_length=1)
    interval_seconds: int | None = Field(
        default=None, ge=_MIN_INTERVAL_SECONDS, le=_MAX_INTERVAL_SECONDS
    )
    enabled: bool | None = None


def _schedules_repo(request: Request) -> SchedulesRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SchedulesRepository(db, settings.scheduled_commands_collection)


def _sessions_repo(request: Request) -> SessionsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SessionsRepository(db, settings.sessions_collection)


@router.post(
    "/sessions/{session_id}/schedules",
    response_model=ScheduleOut,
    status_code=201,
)
async def create_schedule(
    request: Request, session_id: str, body: ScheduleCreate
) -> ScheduleOut:
    if await _sessions_repo(request).get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    doc = await _schedules_repo(request).create(
        session_id, body.text.strip(), body.interval_seconds
    )
    return ScheduleOut.from_doc(doc)


@router.get("/sessions/{session_id}/schedules", response_model=ScheduleListOut)
async def list_schedules(request: Request, session_id: str) -> ScheduleListOut:
    docs = await _schedules_repo(request).list_for_session(session_id)
    items = [ScheduleOut.from_doc(d) for d in docs]
    return ScheduleListOut(items=items, total=len(items))


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def patch_schedule(
    request: Request, schedule_id: str, body: SchedulePatch
) -> ScheduleOut:
    text = body.text.strip() if body.text is not None else None
    doc = await _schedules_repo(request).update(
        schedule_id,
        text=text,
        interval_seconds=body.interval_seconds,
        enabled=body.enabled,
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ScheduleOut.from_doc(doc)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(request: Request, schedule_id: str) -> None:
    ok = await _schedules_repo(request).delete(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
