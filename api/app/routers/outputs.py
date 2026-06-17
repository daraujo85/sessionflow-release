"""Read endpoint for terminal output lines: ``GET /sessions/{id}/output`` (DASH-03).

Lives in its own router (separate from ``sessions.py``) but shares the
``/sessions`` prefix; FastAPI happily merges routers on the same prefix. Returns
terminal lines with ``seq`` greater than an optional ``after`` cursor, ordered
ascending and capped by ``limit`` — enabling incremental polling by the
front-end.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.repositories.output_repo import OutputRepository
from app.repositories.sessions_repo import SessionsRepository

router = APIRouter(prefix="/sessions", tags=["outputs"])


class OutputLineOut(BaseModel):
    """Serialized terminal output line returned by the API."""

    id: str
    session_id: str | None = None
    tmux_name: str | None = None
    seq: int | None = None
    text: str | None = None
    line_type: str | None = None
    at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> OutputLineOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        return cls.model_validate(data)


class OutputListOut(BaseModel):
    """Envelope for the output line list."""

    items: list[OutputLineOut] = Field(default_factory=list)
    total: int = 0


def _get_repo(request: Request) -> OutputRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return OutputRepository(db, settings.output_collection)


@router.get("/{session_id}/output", response_model=OutputListOut)
async def list_output(
    request: Request,
    session_id: str,
    after: int | None = Query(
        default=None, description="Only return lines with seq greater than this."
    ),
    limit: int = Query(default=200, ge=1, le=1000),
) -> OutputListOut:
    # A captura grava `session_id == tmux_name`; a rota recebe o _id do Mongo.
    # Resolve _id -> tmux_name antes de consultar o output (com fallback ao
    # próprio valor recebido, caso já seja um tmux_name).
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    session = await SessionsRepository(db, settings.sessions_collection).get_session(
        session_id
    )
    key = session.get("tmux_name", session_id) if session else session_id

    repo = _get_repo(request)
    docs = await repo.list_output(key, after=after, limit=limit)
    items = [OutputLineOut.from_doc(doc) for doc in docs]
    return OutputListOut(items=items, total=len(items))
