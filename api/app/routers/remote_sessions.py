"""Sessões de OUTRAS contas — o dono cola aqui o link de convidado (`/s/{id}
?k=...`) que outra pessoa (Lucas, Everton, Alvarenga...) compartilhou, e ela
passa a aparecer na SUA lista de Sessões, visualmente destacada como "não é
minha". Ao clicar, abre num iframe (o link de convidado já funciona sozinho,
sem login — é exatamente o mesmo mecanismo do `ShareLink` existente, só que
agora embedado dentro do MEU app em vez de aberto solto numa aba).

Não há nenhuma ponte de dados entre as duas instâncias: é só um bookmark
local (label + URL). A reciprocidade ("ele também vê a minha") é cada lado
colar o link do outro na própria lista — nenhuma conta sabe da existência da
outra além disso.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.repositories.remote_sessions_repo import RemoteSessionsRepository
from app.timeutil import utc_aware_fields

router = APIRouter(tags=["remote-sessions"])


class RemoteSessionIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=2048)


class RemoteSessionOut(BaseModel):
    id: str
    label: str
    url: str
    created_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> RemoteSessionOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        data = utc_aware_fields(data, "created_at")
        return cls.model_validate(data)


class RemoteSessionListOut(BaseModel):
    items: list[RemoteSessionOut]
    total: int


def _repo(request: Request) -> RemoteSessionsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return RemoteSessionsRepository(db, settings.remote_sessions_collection)


@router.get("/remote-sessions", response_model=RemoteSessionListOut)
async def list_remote_sessions(request: Request) -> RemoteSessionListOut:
    docs = await _repo(request).list_all()
    items = [RemoteSessionOut.from_doc(d) for d in docs]
    return RemoteSessionListOut(items=items, total=len(items))


@router.post("/remote-sessions", response_model=RemoteSessionOut, status_code=201)
async def create_remote_session(
    request: Request, body: RemoteSessionIn
) -> RemoteSessionOut:
    url = body.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="URL precisa começar com http:// ou https://")
    doc = await _repo(request).create(label=body.label.strip(), url=url)
    return RemoteSessionOut.from_doc(doc)


@router.delete("/remote-sessions/{remote_id}", status_code=204)
async def delete_remote_session(request: Request, remote_id: str) -> None:
    doc = await _repo(request).delete(remote_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Remote session not found")
