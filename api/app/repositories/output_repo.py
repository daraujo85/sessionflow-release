"""Read-only repository for terminal output lines (``session_output``).

Output documents are written by the Worker with the shape::

    {session_id, tmux_name, seq, text, line_type, at}

where ``line_type`` is one of ``cmd|sys|agent|tool|out|ask``. The collection
name is taken from settings (``output_collection``) so tests can inject an
isolated collection within the ``sessionflow`` database.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


class OutputRepository:
    """Reads terminal output lines from a configurable Mongo collection."""

    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "session_output"
    ) -> None:
        self._collection = db[collection_name]

    async def list_output(
        self,
        session_id: str,
        after: int | None = None,
        before: int | None = None,
        tail: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return output lines for ``session_id`` ordered by ``seq`` ascending.

        ``after`` (seq > after, forward/live tailing), ``before`` (seq <
        before, backward/history) and ``tail`` (most recent ``limit`` lines,
        no cursor — seeds a history view) are mutually exclusive, checked in
        that order. ``before``/``tail`` fetch capped from the right end
        (sorted desc, limited) then re-sort ascending for natural read order.
        """
        query: dict[str, Any] = {"session_id": session_id}
        if after is not None:
            query["seq"] = {"$gt": after}
            cursor = self._collection.find(query).sort("seq", 1).limit(limit)
            return await cursor.to_list(length=limit)

        if before is not None:
            query["seq"] = {"$lt": before}
            cursor = self._collection.find(query).sort("seq", -1).limit(limit)
            docs = await cursor.to_list(length=limit)
            docs.reverse()
            return docs

        if tail:
            cursor = self._collection.find(query).sort("seq", -1).limit(limit)
            docs = await cursor.to_list(length=limit)
            docs.reverse()
            return docs

        cursor = self._collection.find(query).sort("seq", 1).limit(limit)
        return await cursor.to_list(length=limit)
