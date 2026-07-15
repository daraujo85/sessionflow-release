"""Read-only repository for session documents stored in Mongo (motor).

The collection name is taken from settings (``sessions_collection``) so tests
can inject an isolated collection within the ``sessionflow`` database.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase


class SessionsRepository:
    """Reads session documents from a configurable Mongo collection."""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "sessions") -> None:
        self._collection = db[collection_name]

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return all sessions, optionally filtered by exact ``status``."""
        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = status

        cursor = self._collection.find(query).sort("created_at", -1)
        return [doc async for doc in cursor]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single session by id, or ``None`` if not found.

        Returns ``None`` for malformed ids so callers can map both the
        "invalid id" and "not found" cases to a coherent 404.
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return None

        return await self._collection.find_one({"_id": oid})

    async def delete_session(self, session_id: str) -> bool:
        """Remove o doc da sessão pelo id. Retorna True se removeu algo.

        Usado no purge (eliminar) para a sessão sumir da lista NA HORA, sem
        esperar o worker processar o comando assíncrono (evita o flicker de
        'apaguei e voltou'). O worker ainda mata o tmux + limpa dados ligados.
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.delete_one({"_id": oid})
        return res.deleted_count > 0

    async def mark_stopped(self, session_id: str) -> bool:
        """Marca a sessão como ``stopped`` diretamente (sem depender do worker).

        Usado quando o host da sessão está offline: não há worker vivo pra
        processar o comando ``kill``, então o usuário ficaria com a sessão
        presa em "running/detached" pra sempre. Best-effort — não mata
        processo real (o host já não está acessível de qualquer forma).
        """
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid},
            {"$set": {"status": "stopped", "agent_pid": None}},
        )
        return res.matched_count > 0

    async def set_share(
        self, session_id: str, token: str, expires_at: Any
    ) -> bool:
        """Grava/rotaciona o token de link compartilhável + sua validade."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid},
            {"$set": {"share_token": token, "share_expires_at": expires_at}},
        )
        return res.matched_count > 0

    async def clear_share(self, session_id: str) -> bool:
        """Revoga o link: remove token + validade do doc da sessão."""
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.update_one(
            {"_id": oid},
            {"$unset": {"share_token": "", "share_expires_at": ""}},
        )
        return res.modified_count > 0

    async def active_with_name_exists(self, name: str) -> bool:
        """Return True if an ACTIVE session (status != stopped) uses ``name``.

        Matches against either ``tmux_name`` or ``display_name`` so an
        optimistic duplicate check can reject re-creating a live session.
        """
        query: dict[str, Any] = {
            "status": {"$ne": "stopped"},
            "$or": [{"tmux_name": name}, {"display_name": name}],
        }
        return await self._collection.find_one(query) is not None
