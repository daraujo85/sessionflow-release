"""Fila de demandas (orquestrador síncrono por sessão/projeto).

CRUD simples sobre a coleção `demands` — sem fila nova no RabbitMQ. A
sessão de agente que processa a fila (qualquer sessão ativa, instruída pelo
usuário) segue o protocolo em `docs/fila-de-demandas-protocolo.md`: pega a
`pending` mais antiga, marca `in_progress`, clona (`POST
/sessions/{id}/clone`) e manda o texto como instrução, aguarda milestones via
`GET /tasks?session=`, e finaliza via `PATCH /demands/{id}`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.repositories.demands_repo import DemandsRepository
from app.repositories.sessions_repo import SessionsRepository
from app.timeutil import utc_aware_fields

router = APIRouter(tags=["demands"])

DemandStatus = Literal["pending", "in_progress", "completed", "error"]


class DemandOut(BaseModel):
    """Serialized demand document."""

    id: str
    base_session_id: str
    text: str
    status: DemandStatus
    target_session_id: str | None = None
    branch: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str | None = None
    error_note: str | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> DemandOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        data = utc_aware_fields(data, "created_at", "started_at", "completed_at")
        return cls.model_validate(data)


class DemandListOut(BaseModel):
    items: list[DemandOut] = Field(default_factory=list)
    total: int = 0


class DemandCreate(BaseModel):
    text: str = Field(min_length=1)


class DemandPatch(BaseModel):
    """Todos os campos opcionais — o orquestrador manda só o que muda."""

    status: DemandStatus | None = None
    text: str | None = None
    target_session_id: str | None = None
    branch: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str | None = None
    error_note: str | None = None


def _demands_repo(request: Request) -> DemandsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return DemandsRepository(db, settings.demands_collection)


def _sessions_repo(request: Request) -> SessionsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SessionsRepository(db, settings.sessions_collection)


@router.post(
    "/sessions/{base_id}/demands", response_model=DemandOut, status_code=201
)
async def create_demand(request: Request, base_id: str, body: DemandCreate) -> DemandOut:
    if await _sessions_repo(request).get_session(base_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    doc = await _demands_repo(request).create(base_id, body.text.strip())
    return DemandOut.from_doc(doc)


@router.get("/sessions/{base_id}/demands", response_model=DemandListOut)
async def list_demands(
    request: Request,
    base_id: str,
    status: DemandStatus | None = Query(default=None),
) -> DemandListOut:
    docs = await _demands_repo(request).list_for_base(base_id, status=status)
    items = [DemandOut.from_doc(d) for d in docs]
    return DemandListOut(items=items, total=len(items))


_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress"},
    "in_progress": {"completed", "error"},
    "completed": set(),
    "error": set(),
}


@router.patch("/demands/{demand_id}", response_model=DemandOut)
async def patch_demand(request: Request, demand_id: str, body: DemandPatch) -> DemandOut:
    fields = body.model_dump(exclude_unset=True)
    repo = _demands_repo(request)

    if fields.get("target_session_id") is not None:
        target_ok = await _sessions_repo(request).get_session(fields["target_session_id"])
        if target_ok is None:
            raise HTTPException(status_code=404, detail="Target session not found")

    if "text" in fields:
        text = (fields.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="text cannot be empty")
        fields["text"] = text

    expected_status: str | None = None
    if "status" in fields or "text" in fields:
        current = await repo.get(demand_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Demand not found")
        current_status = current["status"]
        if "status" in fields:
            new_status = fields["status"]
            if new_status not in _VALID_TRANSITIONS.get(current_status, set()):
                raise HTTPException(
                    status_code=409,
                    detail=f"Invalid transition: {current_status} -> {new_status}",
                )
        if "text" in fields and current_status != "pending":
            # Edição de texto só faz sentido antes da demanda ser "pega" pelo
            # orquestrador (ver docs/fila-de-demandas-protocolo.md, fora de
            # escopo: sem editar demanda in_progress).
            raise HTTPException(
                status_code=409,
                detail="Cannot edit text of a demand that is not pending",
            )
        expected_status = current_status

    doc = await repo.update(demand_id, expected_status=expected_status, **fields)
    if doc is None:
        if expected_status is not None:
            raise HTTPException(
                status_code=409, detail="Demand status changed concurrently"
            )
        raise HTTPException(status_code=404, detail="Demand not found")
    return DemandOut.from_doc(doc)


@router.delete("/demands/{demand_id}", status_code=204)
async def delete_demand(request: Request, demand_id: str) -> None:
    repo = _demands_repo(request)
    current = await repo.get(demand_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Demand not found")
    if current["status"] != "pending":
        # Mesma regra do PATCH de texto: só dá pra excluir antes de ser
        # pega pelo orquestrador (in_progress/completed/error ficam
        # registradas na fila pro usuário revisar depois).
        raise HTTPException(
            status_code=409, detail="Cannot delete a demand that is not pending"
        )
    deleted = await repo.delete(demand_id, expected_status="pending")
    if not deleted:
        raise HTTPException(
            status_code=409, detail="Demand status changed concurrently"
        )
