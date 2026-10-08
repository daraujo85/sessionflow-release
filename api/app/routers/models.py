"""Read endpoint for the REAL per-agent host models: ``GET /models`` (MODEL-01).

The model lists are discovered on the host by the Worker (real CLIs / configs,
never hardcoded) and stored in ``host_models``. The front-end's "Create session"
model picker reads from here.

- ``GET /models`` -> all agents.
- ``GET /models?agent=claude`` -> just that agent (empty envelope if unknown).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.repositories.models_repo import ModelsRepository

router = APIRouter(prefix="/models", tags=["models"])


class ModelOut(BaseModel):
    """A single model offered by an agent."""

    id: str
    label: str
    description: str | None = None
    is_default: bool = False


class AgentModelsOut(BaseModel):
    """Model list for one agent."""

    agent: str
    models: list[ModelOut] = Field(default_factory=list)
    source: str | None = None


class ModelsListOut(BaseModel):
    """Envelope for the (one or many) agent model lists."""

    items: list[AgentModelsOut] = Field(default_factory=list)


def _get_repo(request: Request) -> ModelsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return ModelsRepository(db, settings.models_collection)


@router.get("", response_model=ModelsListOut)
async def list_models(
    request: Request,
    agent: str | None = Query(default=None, description="Filter to a single agent"),
) -> ModelsListOut:
    repo = _get_repo(request)
    if agent:
        doc = await repo.get_for_agent(agent)
        docs = [doc] if doc else []
    else:
        docs = await repo.list_all()
    items = [AgentModelsOut.model_validate(doc) for doc in docs]
    return ModelsListOut(items=items)
