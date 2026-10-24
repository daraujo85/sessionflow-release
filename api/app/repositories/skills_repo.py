"""Repositories do catálogo de skills (``docs/skills-distribution-contract.md``).

``skills`` (1 doc por skill)::

    {_id: skill_id, name, scope, description, latest_version_id,
     origin_host, origin_path, created_at, updated_at}

``skill_versions`` (1 doc por skill_id+version_id, imutável)::

    {_id: "<skill_id>@<version_id>", skill_id, version_id, content_hash,
     package_sha256, size, manifest, drive_file_id, status, origin_host,
     origin_path, published_at}

"Mais recente" = maior ``published_at`` com ``status=published``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

PUBLISHED = "published"

def version_doc_id(skill_id: str, version_id: str) -> str:
    return f"{skill_id}@{version_id}"

class SkillsRepository:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "skills") -> None:
        self._collection = db[collection_name]

    async def list(self, q: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}
            query = {"$or": [{"_id": rx}, {"name": rx}, {"description": rx}]}
        cursor = self._collection.find(query).sort("_id", 1)
        return [doc async for doc in cursor]

    async def get(self, skill_id: str) -> dict[str, Any] | None:
        return await self._collection.find_one({"_id": skill_id})

    async def upsert_published(
        self, meta: dict[str, Any], version_id: str, published_at: datetime
    ) -> None:
        """Atualiza metadados e aponta ``latest_version_id`` pra versão recém
        publicada — só se ela for mais nova que a atual (publicação concorrente
        mais antiga não regride o latest)."""
        fields = {
            "name": meta["name"],
            "scope": meta["scope"],
            "description": meta.get("description") or "",
            "origin_host": meta.get("origin_host"),
            "origin_path": meta.get("origin_path"),
            "updated_at": published_at,
        }
        await self._collection.update_one(
            {"_id": meta["skill_id"]},
            {"$set": fields, "$setOnInsert": {"created_at": published_at}},
            upsert=True,
        )
        await self._collection.update_one(
            {
                "_id": meta["skill_id"],
                "$or": [
                    {"latest_published_at": {"$exists": False}},
                    {"latest_published_at": {"$lte": published_at}},
                ],
            },
            {"$set": {"latest_version_id": version_id, "latest_published_at": published_at}},
        )

class SkillVersionsRepository:
    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "skill_versions"
    ) -> None:
        self._collection = db[collection_name]

    async def get(self, skill_id: str, version_id: str) -> dict[str, Any] | None:
        return await self._collection.find_one(
            {"_id": version_doc_id(skill_id, version_id), "status": PUBLISHED}
        )

    async def list_for_skill(self, skill_id: str) -> list[dict[str, Any]]:
        cursor = self._collection.find({"skill_id": skill_id, "status": PUBLISHED}).sort(
            "published_at", -1
        )
        return [doc async for doc in cursor]

    async def latest(self, skill_id: str) -> dict[str, Any] | None:
        cursor = (
            self._collection.find({"skill_id": skill_id, "status": PUBLISHED})
            .sort("published_at", -1)
            .limit(1)
        )
        docs = [doc async for doc in cursor]
        return docs[0] if docs else None

    async def create(self, doc: dict[str, Any]) -> bool:
        """Insere a versão já publicada. False se outra publicação do mesmo
        hash ganhou a corrida (o ``_id`` determinístico garante unicidade)."""
        doc = {
            **doc,
            "_id": version_doc_id(doc["skill_id"], doc["version_id"]),
            "status": PUBLISHED,
            "published_at": doc.get("published_at") or datetime.now(UTC),
        }
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError:
            return False
        return True
