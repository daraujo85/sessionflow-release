"""Perfil do usuário (single-user): foto persistida no servidor.

A foto é guardada NO SERVIDOR (Mongo, coleção ``profile``, doc único
``_id="me"``) — nunca em localStorage — para aparecer em qualquer dispositivo.
O cliente envia um data URL já redimensionado (256px/JPEG), pequeno o bastante
para guardar como string.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/profile", tags=["profile"])

PROFILE_COLLECTION = "profile"
PROFILE_ID = "me"
# Limite defensivo do data URL (256px/JPEG cabe folgado em ~700 KB).
_MAX_PHOTO_CHARS = 900_000


class ProfileOut(BaseModel):
    """Perfil do usuário."""

    photo: str | None = None


class PhotoIn(BaseModel):
    """Corpo do upload da foto (data URL de imagem)."""

    photo: str = Field(min_length=1)


@router.get("", response_model=ProfileOut)
async def get_profile(request: Request) -> ProfileOut:
    db = request.app.state.mongo_db
    doc = await db[PROFILE_COLLECTION].find_one({"_id": PROFILE_ID})
    return ProfileOut(photo=(doc or {}).get("photo"))


@router.put("/photo", response_model=ProfileOut)
async def set_photo(request: Request, body: PhotoIn) -> ProfileOut:
    photo = body.photo.strip()
    if not photo.startswith("data:image/"):
        raise HTTPException(status_code=422, detail="photo deve ser um data URL de imagem")
    if len(photo) > _MAX_PHOTO_CHARS:
        raise HTTPException(status_code=413, detail="imagem muito grande")
    db = request.app.state.mongo_db
    await db[PROFILE_COLLECTION].update_one(
        {"_id": PROFILE_ID},
        {"$set": {"photo": photo, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return ProfileOut(photo=photo)


@router.delete("/photo", response_model=ProfileOut)
async def delete_photo(request: Request) -> ProfileOut:
    db = request.app.state.mongo_db
    await db[PROFILE_COLLECTION].update_one(
        {"_id": PROFILE_ID},
        {"$unset": {"photo": ""}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return ProfileOut(photo=None)
