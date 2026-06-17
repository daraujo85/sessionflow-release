"""Configurações gerais do app (single-user) — coleção ``app_settings``.

Hoje guarda só ``milestones_auto`` (instruir as sessões a trabalhar em
tarefas/marcos automaticamente ao abrir/criar). Doc único ``_id="app"``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

SETTINGS_ID = "app"


def milestones_instruction(session: str) -> str:
    """Instrução (uma linha) injetada na sessão p/ manter os marcos.

    O nome do arquivo é NAMESPACED pela sessão (``milestones.<session>.json``)
    para não colidir quando várias sessões compartilham o mesmo diretório.
    """
    return (
        "[SessionFlow] A partir de agora, trabalhe em tarefas/marcos: mantenha o "
        f"arquivo .sessionflow/milestones.{session}.json na raiz do projeto no "
        'formato {"milestones":[{"id":"<kebab>","title":"<curto>",'
        '"status":"todo|doing|blocked|done"}]}, criando e atualizando o status '
        "conforme avança. O SessionFlow lê esse arquivo para mostrar suas tarefas. "
        f"Use EXATAMENTE esse nome de arquivo (milestones.{session}.json). "
        "Mantenha de 3 a 8 itens e não remova os concluídos."
    )


class SettingsOut(BaseModel):
    """Configurações expostas ao app."""

    milestones_auto: bool = True


class SettingsIn(BaseModel):
    """Atualização das configurações."""

    milestones_auto: bool


async def read_settings(request: Request) -> SettingsOut:
    """Lê o doc de settings (default: tudo ligado)."""
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    doc = await db[settings.app_settings_collection].find_one({"_id": SETTINGS_ID})
    if not doc:
        return SettingsOut()
    return SettingsOut(milestones_auto=bool(doc.get("milestones_auto", True)))


@router.get("", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    return await read_settings(request)


@router.put("", response_model=SettingsOut)
async def put_settings(request: Request, body: SettingsIn) -> SettingsOut:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.app_settings_collection].update_one(
        {"_id": SETTINGS_ID},
        {"$set": {"milestones_auto": body.milestones_auto}},
        upsert=True,
    )
    return SettingsOut(milestones_auto=body.milestones_auto)
