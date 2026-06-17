"""Repository for upload metadata documents stored in Mongo (motor).

An upload document records a file received by the API (e.g. an audio file
uploaded for a session) with the shape::

    {session_id, path, kind, status, created_at}

The collection name is taken from settings (``uploads_collection``) so tests
can inject an isolated collection within the ``sessionflow`` database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorDatabase


class UploadsRepository:
    """Creates upload documents in a configurable Mongo collection."""

    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "uploads"
    ) -> None:
        self._collection = db[collection_name]

    async def create_upload(
        self,
        session_id: str,
        path: str,
        kind: str = "audio",
        status: str = "received",
    ) -> str:
        """Insert an upload document and return its id (string)."""
        doc = {
            "session_id": session_id,
            "path": path,
            "kind": kind,
            "status": status,
            "created_at": datetime.now(UTC),
        }
        result = await self._collection.insert_one(doc)
        return str(result.inserted_id)
