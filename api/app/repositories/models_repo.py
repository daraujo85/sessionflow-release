"""Repository for the REAL per-agent host model lists (MODEL-01).

Model documents are written by the Worker's ``model_discovery`` into the
``host_models`` collection, one document per agent::

    {agent, models:[{id, label, description, is_default}], source, scanned_at}

``agent`` is unique. ``source`` ∈ {``config``, ``picker``, ``fallback``}.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

# Fields returned to the client (drop Mongo ``_id``).
_PROJECTION = {"_id": 0, "agent": 1, "models": 1, "source": 1, "scanned_at": 1}


class ModelsRepository:
    """Read access to the host models collection."""

    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "host_models"
    ) -> None:
        self._collection = db[collection_name]

    async def list_all(self) -> list[dict[str, Any]]:
        """Return the model document for every agent, ordered by ``agent``."""
        cursor = self._collection.find({}, _PROJECTION).sort("agent", 1)
        return await cursor.to_list(length=None)

    async def get_for_agent(self, agent: str) -> dict[str, Any] | None:
        """Return the model document for a single agent, or ``None``."""
        return await self._collection.find_one({"agent": agent}, _PROJECTION)
