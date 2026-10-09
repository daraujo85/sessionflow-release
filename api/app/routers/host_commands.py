"""Rotas P2P host-to-host (sem sessão Claude).

Comunicação direta entre máquinas via SessionFlow backend: o requester
publica um comando via RabbitMQ pro host alvo, o worker daquele host
executa e grava o resultado em ``command_results``, e o requester faz
polling em ``GET /commands/{command_id}`` até receber.

Endpoints hoje:
- ``POST /hosts/{host_id}/fetch`` — publica ``fs_fetch`` (ler 1 arquivo do host)
- ``GET /commands/{command_id}``  — busca o resultado do comando

NÃO há sessão Claude envolvida — só host + worker. O (multi-host, AD-011)
garante que cada worker só consome comandos roteados pro SEU host_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.publishers.command_publisher import publish_command

router = APIRouter(tags=["host_commands"])

COMMAND_RESULTS_COLLECTION = "command_results"


class FetchIn(BaseModel):
    """Body do POST /hosts/{host_id}/fetch."""

    path: str = Field(min_length=1, description="Path absoluto do arquivo a ler")
    # YAGNI: max_bytes, offset, encoding override ficam pra depois se alguém pedir.


class FetchAccepted(BaseModel):
    command_id: str
    status: str = "queued"
    host_id: str


class CommandOut(BaseModel):
    """Estado de um comando P2P."""

    id: str
    status: str  # pending | ok | error
    host_id: str | None = None
    type: str | None = None
    ok: bool | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    finished_at: datetime | None = None
    requested_at: datetime | None = None


@router.post(
    "/hosts/{host_id}/fetch", response_model=FetchAccepted, status_code=202
)
async def post_fetch(
    request: Request, host_id: str, body: FetchIn
) -> FetchAccepted:
    """Pede ao worker de ``host_id`` para ler 1 arquivo local.

    O worker resolve o path (com guard de $HOME/tmp) e grava o resultado em
    ``command_results._id=command_id``. O cliente polling em
    ``GET /commands/{command_id}`` vê ``ok=true``+``result`` quando termina
    ou ``ok=false``+``error`` quando falha.

    Falha se o host alvo nunca fez heartbeat (worker offline) — não vale a
    pena enfileirar, ninguém vai consumir.
    """
    db = request.app.state.mongo_db
    coll = db["worker_status"]
    worker = await coll.find_one({"_id": host_id})
    if worker is None:
        raise HTTPException(status_code=404, detail=f"host desconhecido: {host_id}")
    # Heartbeat velho (> 5min) = worker efetivamente morto. Evita enfileirar
    # comandos que nunca serão consumidos (polling eterno pro cliente).
    updated = worker.get("updated_at")
    if updated is not None:
        # Mongo retorna naive em docs antigos (pré timezone-aware). Normaliza
        # pra UTC antes de subtrair — nunca comparar naive com aware.
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        if age > 300:
            raise HTTPException(
                status_code=409,
                detail=f"worker {host_id} offline há {int(age)}s; nada vai consumir o comando",
            )

    settings = request.app.state.settings
    command_id = await publish_command(
        settings,
        type="fs_fetch",
        payload={"path": body.path},
        host_id=host_id,
    )
    return FetchAccepted(command_id=command_id, host_id=host_id)


@router.get("/commands/{command_id}", response_model=CommandOut)
async def get_command(request: Request, command_id: str) -> CommandOut:
    """Busca resultado de um comando P2P.

    Status:
    - ``pending``: enfileirado, worker ainda não terminou (sem doc em command_results)
    - ``ok``:      sucesso — ``result`` populado
    - ``error``:   falhou — ``error`` populado, ``result`` None
    """
    db = request.app.state.mongo_db
    doc = await db[COMMAND_RESULTS_COLLECTION].find_one({"_id": command_id})
    if doc is None:
        return CommandOut(id=command_id, status="pending")
    return CommandOut(
        id=command_id,
        status="ok" if doc.get("ok") else "error",
        host_id=doc.get("host_id"),
        type=doc.get("type"),
        ok=doc.get("ok"),
        result=doc.get("result"),
        error=doc.get("error"),
        finished_at=doc.get("finished_at"),
    )