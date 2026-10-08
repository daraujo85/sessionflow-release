"""Repository for files the AGENT shares back with the user (reverse of the
manual upload flow — see ``tools/sf share`` and ``routers/shared_files.py``).

Doc shape::

    {session_id, filename, stored_path, content_type, size, created_at}

The collection name is taken from settings (``shared_files_collection``) so
tests can inject an isolated collection within the ``sessionflow`` database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase


class SharedFilesRepository:
    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "shared_files"
    ) -> None:
        self._collection = db[collection_name]

    async def create(
        self,
        session_id: str,
        filename: str,
        stored_path: str,
        content_type: str,
        size: int,
    ) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "session_id": session_id,
            "filename": filename,
            "stored_path": stored_path,
            "content_type": content_type,
            "size": size,
            "created_at": datetime.now(UTC),
        }
        res = await self._collection.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self._collection.find({"session_id": session_id}).sort("created_at", -1)
        return [doc async for doc in cursor]

    async def get(self, file_id: str) -> dict[str, Any] | None:
        try:
            oid = ObjectId(file_id)
        except (InvalidId, TypeError):
            return None
        return await self._collection.find_one({"_id": oid})

    async def delete(self, file_id: str) -> dict[str, Any] | None:
        """Remove o doc e retorna ele (pra quem chamou apagar o arquivo físico)."""
        try:
            oid = ObjectId(file_id)
        except (InvalidId, TypeError):
            return None
        doc = await self._collection.find_one_and_delete({"_id": oid})
        return doc
