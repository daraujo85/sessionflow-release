"""Read endpoints for activity history, notifications and tasks (DASH-09/10).

Exposes three endpoints backed by the Worker-written ``events`` and ``tasks``
collections:

* ``GET /events/history?day=YYYY-MM-DD`` — events for a day (or all), desc.
* ``GET /notifications`` — recent events with a known notification ``kind``.
* ``GET /tasks?session=`` — tasks, optionally filtered by session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

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


@router.get("/notifications", response_model=EventListOut)
async def notifications(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> EventListOut:
    repo = _notification_repo(request)
    docs = await repo.notifications(limit=limit)
    items = [EventOut.from_doc(doc) for doc in docs]
    return EventListOut(items=items, total=len(items))


@router.get("/tasks", response_model=TaskListOut)
async def list_tasks(
    request: Request,
    session: str | None = Query(default=None, description="Filter by session id."),
) -> TaskListOut:
    repo = _task_repo(request)
    docs = await repo.list_tasks(session_id=session)
    items = [TaskOut.from_doc(doc) for doc in docs]
    return TaskListOut(items=items, total=len(items))
