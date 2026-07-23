"""Repository para "sessões remotas" — bookmarks de links de sessão
COMPARTILHADOS por outra pessoa (outra conta/instância SessionFlow), que o
dono desta conta quer ver junto da própria lista (ver ``routers/remote_sessions.py``).

Doc shape::

    {label, url, created_at}

Não guardamos nada além do link em si: o card na lista só embeda ``url``
(que já é o link de convidado `{origin}/s/{id}?k=...`) num iframe — a conta
remota nem sabe que foi "adicionada", é só um bookmark local.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase


class RemoteSessionsRepository:
    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "remote_sessions"
    ) -> None:
        self._collection = db[collection_name]

    async def create(self, label: str, url: str) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "label": label,
            "url": url,
            "created_at": datetime.now(UTC),
        }
        res = await self._collection.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def list_all(self) -> list[dict[str, Any]]:
        cursor = self._collection.find({}).sort("created_at", 1)
        return [doc async for doc in cursor]

    async def delete(self, remote_id: str) -> dict[str, Any] | None:
        try:
            oid = ObjectId(remote_id)
        except (InvalidId, TypeError):
            return None
        return await self._collection.find_one_and_delete({"_id": oid})
