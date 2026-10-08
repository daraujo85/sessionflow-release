"""Read-only repository for session tasks (``tasks``).

Task documents are written by the Worker with the shape::

    {session_id, title, state, updated_at}

where ``state`` is one of ``todo|doing|blocked|done|attention``. The
collection name is taken from settings so tests can inject an isolated
collection within the ``sessionflow`` database.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


class TaskRepository:
    """Reads task documents from a configurable Mongo collection."""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "tasks") -> None:
        self._collection = db[collection_name]

    async def list_tasks(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Return tasks ordered by ``updated_at`` descending.

        When ``session_id`` is given, only that session's tasks are returned.
        """
        query: dict[str, Any] = {}
        if session_id is not None:
            query["session_id"] = session_id

        cursor = self._collection.find(query).sort("updated_at", -1)
        return await cursor.to_list(length=None)
