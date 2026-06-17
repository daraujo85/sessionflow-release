"""Read endpoint for scanned host directories: ``GET /directories`` (TMUX-08).

Used by the front-end work-dir picker: returns a small set of directory
suggestions matching a substring query, or the most recent directories when
the query is empty.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.repositories.directories_repo import DirectoriesRepository

router = APIRouter(prefix="/directories", tags=["directories"])


class DirectoryOut(BaseModel):
    """Serialized directory suggestion returned by the API."""

    path: str
    parent: str | None = None
    name: str | None = None
    root: str | None = None


class DirectoryListOut(BaseModel):
    """Envelope for the directory list."""

    items: list[DirectoryOut] = Field(default_factory=list)
    no_match: bool = False


def _get_repo(request: Request) -> DirectoriesRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return DirectoriesRepository(db, settings.host_directories_collection)


@router.get("", response_model=DirectoryListOut)
async def search_directories(
    request: Request,
    q: str = Query(default="", description="Substring to match on path/name"),
    limit: int = Query(default=6, ge=1, le=50),
) -> DirectoryListOut:
    repo = _get_repo(request)
    docs = await repo.search(q, limit=limit)
    items = [DirectoryOut.model_validate(doc) for doc in docs]
    no_match = bool(q.strip()) and not items
    return DirectoryListOut(items=items, no_match=no_match)
