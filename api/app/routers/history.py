"""Read endpoints for activity history, notifications and tasks (DASH-09/10).

Exposes three endpoints backed by the Worker-written ``events`` and ``tasks``
collections:

* ``GET /events/history?day=YYYY-MM-DD`` — events for a day (or all), desc.
* ``GET /notifications`` — recent events with a known notification ``kind``.
* ``GET /tasks?session=`` — tasks, optionally filtered by session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.publishers.command_publisher import publish_command
from app.repositories.event_repo import EventRepository
from app.repositories.task_repo import TaskRepository

router = APIRouter(tags=["history"])


class EventOut(BaseModel):
    """Serialized event document returned by the API."""

    id: str
    session_id: str | None = None
    type: str | None = None
    kind: str | None = None
    title: str | None = None
    desc: str | None = None
    at: datetime | None = None
    seq: int | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> EventOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        return cls.model_validate(data)


class EventListOut(BaseModel):
    """Envelope for the event list."""

    items: list[EventOut] = Field(default_factory=list)
    total: int = 0


class TaskOut(BaseModel):
    """Serialized task document returned by the API."""

    id: str
    session_id: str | None = None
    title: str | None = None
    state: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> TaskOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        return cls.model_validate(data)


class TaskListOut(BaseModel):
    """Envelope for the task list."""

    items: list[TaskOut] = Field(default_factory=list)
    total: int = 0


def _event_repo(request: Request) -> EventRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return EventRepository(db, settings.events_collection)


def _notification_repo(request: Request) -> EventRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return EventRepository(db, settings.notifications_collection)


def _task_repo(request: Request) -> TaskRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return TaskRepository(db, settings.tasks_collection)


@router.get("/events/history", response_model=EventListOut)
async def events_history(
    request: Request,
    day: str | None = Query(
        default=None, description="Filter to a single UTC day (YYYY-MM-DD)."
    ),
) -> EventListOut:
    repo = _event_repo(request)
    docs = await repo.history(day=day)
    items = [EventOut.from_doc(doc) for doc in docs]
    return EventListOut(items=items, total=len(items))


# Doc único de configurações do app (mesmo usado pelo router de settings).
_APP_SETTINGS_ID = "app"
_CLEARED_AT_KEY = "notifications_cleared_at"


@router.get("/notifications", response_model=EventListOut)
async def notifications(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> EventListOut:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    # Watermark do "Limpar todas": só notificações mais novas que ele aparecem.
    doc = await db[settings.app_settings_collection].find_one({"_id": _APP_SETTINGS_ID})
    since = doc.get(_CLEARED_AT_KEY) if doc else None
    repo = _notification_repo(request)
    docs = await repo.notifications(limit=limit, since=since)
    items = [EventOut.from_doc(doc) for doc in docs]
    return EventListOut(items=items, total=len(items))


@router.delete("/notifications", status_code=204)
async def clear_notifications(request: Request) -> Response:
    """Limpa TODAS as notificações do sininho — sem destruir nada.

    Grava ``notifications_cleared_at = agora`` em ``app_settings`` (watermark).
    O ``GET /notifications`` passa a só devolver o que for mais novo que isso, e
    o ``GET /events/history`` continua mostrando tudo (o histórico é preservado).
    Idempotente: chamar de novo só avança o marco.
    """
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.app_settings_collection].update_one(
        {"_id": _APP_SETTINGS_ID},
        {"$set": {_CLEARED_AT_KEY: datetime.now(UTC)}},
        upsert=True,
    )
    return Response(status_code=204)


@router.get("/tasks", response_model=TaskListOut)
async def list_tasks(
    request: Request,
    session: str | None = Query(default=None, description="Filter by session id."),
) -> TaskListOut:
    repo = _task_repo(request)
    docs = await repo.list_tasks(session_id=session)
    items = [TaskOut.from_doc(doc) for doc in docs]
    return TaskListOut(items=items, total=len(items))


@router.delete("/tasks/{task_id}", status_code=202)
async def delete_task(request: Request, task_id: str) -> Response:
    """Apaga uma tarefa (marco): some daqui NA HORA e do arquivo no Mac.

    Carrega o doc da tarefa pelo ``_id``; dele tira a sessão (``session_id`` =
    tmux_name) e o id do marco (``milestone_id``, como gravado no JSON). Acha o
    ``work_dir`` na sessão (``tmux_name == session_id``). Remove o doc de
    ``tasks`` imediatamente (some da lista) e publica ``delete_task`` para o
    worker apagar a entrada do arquivo de marcos no host (senão o sync
    re-adicionaria). 404 se a tarefa/sessão não existir.
    """
    settings = request.app.state.settings
    db = request.app.state.mongo_db

    try:
        oid = ObjectId(task_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Task not found")

    tasks_coll = db[settings.tasks_collection]
    task = await tasks_coll.find_one({"_id": oid})
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    session_id = task.get("session_id")
    # Id do marco como está no arquivo de marcos; fallback no título.
    milestone_id = task.get("milestone_id") or task.get("title")
    if not session_id or not milestone_id:
        raise HTTPException(status_code=404, detail="Task not found")

    sessions_coll = db[settings.sessions_collection]
    session = await sessions_coll.find_one({"tmux_name": session_id})
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    work_dir = session.get("work_dir") or ""

    # Remove o doc NA HORA (some da lista sem esperar o worker).
    await tasks_coll.delete_one({"_id": oid})

    await publish_command(
        settings,
        type="delete_task",
        payload={
            "name": session_id,
            "work_dir": work_dir,
            "task_id": milestone_id,
        },
    )
    return Response(status_code=202)
