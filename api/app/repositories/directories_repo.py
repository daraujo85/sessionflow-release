"""Repository for querying scanned host directories.

Directory documents are written by the Worker (T10) into the
``host_directories`` collection with the shape::

    {path, parent, name, root, host_id, scanned_at}

``(host_id, path)`` is the unique key (multi-host, AD-011) — the SAME
relative path (e.g. ``~/dev/portal``) can exist on more than one host.
"""

from __future__ import annotations

import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

# Fields returned to the client.
_PROJECTION = {"_id": 0, "path": 1, "parent": 1, "name": 1, "root": 1}


class DirectoriesRepository:
    """Read access to the host directories collection."""

    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "host_directories"
    ) -> None:
        self._collection = db[collection_name]

    async def search(
        self, query: str, limit: int = 6, host_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Search directories by substring on ``path``/``name``.

        When ``query`` is empty, returns up to ``limit`` directories ordered by
        ``scanned_at`` descending (falling back to ``name``). Otherwise filters
        by a case-insensitive substring match on ``path`` or ``name``.

        ``host_id`` (multi-host, AD-011) escopa a busca pro host ONDE a
        sessão vai ser criada — sem isso, o autocomplete misturaria
        diretórios de máquinas diferentes (ex.: sugerir um caminho do Mac
        pra uma sessão que vai rodar no Windows). ``None`` mantém o
        comportamento antigo (busca em todos — usado só de fallback).
        """
        query = (query or "").strip()
        host_filter: dict[str, Any] = {"host_id": host_id} if host_id else {}

        if not query:
            cursor = (
                self._collection.find(host_filter, _PROJECTION)
                .sort([("scanned_at", -1), ("name", 1)])
                .limit(limit)
            )
            return await cursor.to_list(length=limit)

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        mongo_filter = {
            **host_filter,
            "$or": [{"path": pattern}, {"name": pattern}],
        }
        cursor = self._collection.find(mongo_filter, _PROJECTION).limit(limit)
        return await cursor.to_list(length=limit)
