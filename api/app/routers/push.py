"""Web Push (VAPID): chave pública + subscrições do navegador.

A API expõe a chave pública VAPID (o browser usa no `subscribe`) e guarda as
subscrições em ``push_subscriptions`` (dedupe por ``endpoint``). Quem ENVIA o
push é o Worker (tem a chave privada). Notificação com o app fechado.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/push", tags=["push"])


class VapidOut(BaseModel):
    """Chave pública VAPID (application server key)."""

    public_key: str = ""


class PushSubscription(BaseModel):
    """Subscrição Web Push enviada pelo navegador (PushSubscription.toJSON())."""

    endpoint: str = Field(min_length=1)
    keys: dict[str, str]


@router.get("/vapid", response_model=VapidOut)
async def get_vapid(request: Request) -> VapidOut:
    settings = request.app.state.settings
    return VapidOut(public_key=settings.vapid_public or "")


@router.post("/subscribe", status_code=201)
async def subscribe(request: Request, body: PushSubscription) -> dict:
    if not body.endpoint or not body.keys.get("p256dh") or not body.keys.get("auth"):
        raise HTTPException(status_code=422, detail="subscrição inválida")
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.push_subscriptions_collection].update_one(
        {"endpoint": body.endpoint},
        {
            "$set": {
                "endpoint": body.endpoint,
                "keys": body.keys,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return {"status": "subscribed"}


@router.post("/unsubscribe", status_code=200)
async def unsubscribe(request: Request, body: PushSubscription) -> dict:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.push_subscriptions_collection].delete_one(
        {"endpoint": body.endpoint}
    )
    return {"status": "unsubscribed"}
