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
