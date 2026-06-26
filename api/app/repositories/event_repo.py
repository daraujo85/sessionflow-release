"""Read-only repository for events / notifications (``events``).

Event documents are written by the Worker with the shape::

    {session_id, type, kind, title, desc, at, seq}

where ``kind`` is one of ``attention|info|warning|success``. The collection
name is taken from settings so tests can inject an isolated collection within
the ``sessionflow`` database.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

_NOTIFICATION_KINDS = ("attention", "info", "warning", "success")


class EventRepository:
    """Reads event documents from a configurable Mongo collection."""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "events") -> None:
        self._collection = db[collection_name]

    async def history(self, day: str | None = None) -> list[dict[str, Any]]:
        """Return events ordered by ``at`` descending.

        When ``day`` (``YYYY-MM-DD``) is given, only events whose ``at`` falls
        on that UTC day are returned. Invalid ``day`` values yield an empty
        list.
        """
        query: dict[str, Any] = {}
        if day:
            try:
                parsed = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError:
                return []
            start = datetime.combine(parsed, time.min, tzinfo=UTC)
            end = start + timedelta(days=1)
            query["at"] = {"$gte": start, "$lt": end}

        cursor = self._collection.find(query).sort("at", -1)
        return await cursor.to_list(length=None)

    async def notifications(
        self, limit: int = 50, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Return recent events whose ``kind`` is a known notification kind.

        ``since`` é o marco de "limpo até aqui" (watermark): quando informado,
        só retorna notificações com ``at`` MAIS NOVO que ele. É assim que o
        "Limpar todas" funciona sem apagar nada — o histórico (``events``)
        continua intacto, só o sininho passa a ignorar o que veio antes.
        """
        query: dict[str, Any] = {"kind": {"$in": list(_NOTIFICATION_KINDS)}}
        if since is not None:
            query["at"] = {"$gt": since}
        cursor = self._collection.find(query).sort("at", -1).limit(limit)
        return await cursor.to_list(length=limit)
