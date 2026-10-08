"""Repository for the demand queue (fila de demandas).

Cada doc é uma demanda digitada pelo usuário numa sessão/projeto
(`base_session_id`). Uma sessão de agente ativa processa a fila, uma
demanda por vez — o loop em si é um protocolo documentado (ver
``docs/fila-de-demandas-protocolo.md``), não um serviço em background.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase


class DemandsRepository:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str = "demands") -> None:
        self._collection = db[collection_name]

    async def create(self, base_session_id: str, text: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc: dict[str, Any] = {
            "base_session_id": base_session_id,
            "text": text,
            "status": "pending",
            "target_session_id": None,
            "branch": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "summary": None,
            "error_note": None,
        }
        res = await self._collection.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def list_for_base(
        self, base_session_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"base_session_id": base_session_id}
        if status is not None:
            query["status"] = status
        cursor = self._collection.find(query).sort("created_at", -1)
        return [doc async for doc in cursor]

    @staticmethod
    def _to_oid(demand_id: str) -> ObjectId | None:
        try:
            return ObjectId(demand_id)
        except (InvalidId, TypeError):
            return None

    async def get(self, demand_id: str) -> dict[str, Any] | None:
        oid = self._to_oid(demand_id)
        if oid is None:
            return None
        return await self._collection.find_one({"_id": oid})

    async def delete(self, demand_id: str, *, expected_status: str | None = None) -> bool:
        """Remove a demanda. Com ``expected_status``, só apaga se o status
        atual bater (compare-and-delete) — evita apagar uma demanda que
        acabou de entrar em execução entre o GET e o DELETE do caller."""
        oid = self._to_oid(demand_id)
        if oid is None:
            return False
        query: dict[str, Any] = {"_id": oid}
        if expected_status is not None:
            query["status"] = expected_status
        res = await self._collection.delete_one(query)
        return res.deleted_count > 0

    async def update(
        self, demand_id: str, *, expected_status: str | None = None, **fields: Any
    ) -> dict[str, Any] | None:
        """Seta só as chaves passadas em ``fields`` (mesmo que o valor seja
        ``None`` — o caller decide o que "passar" via ``exclude_unset``).

        Se ``expected_status`` for passado, o update só aplica quando o status
        atual no banco bater com ele (compare-and-set) — usado pelo router
        pra evitar duas transições concorrentes na mesma demanda (ex.: dois
        loops do orquestrador tentando marcar a mesma `pending` como
        `in_progress` ao mesmo tempo). Se não bater (ou o doc não existir),
        retorna ``None`` — o caller distingue "não existe" de "mudou
        embaixo do pé" comparando com um `get()` anterior, se precisar.
        """
        oid = self._to_oid(demand_id)
        if oid is None:
            return None
        if fields:
            query: dict[str, Any] = {"_id": oid}
            if expected_status is not None:
                query["status"] = expected_status
            res = await self._collection.update_one(query, {"$set": fields})
            if res.matched_count == 0:
                return None
        return await self._collection.find_one({"_id": oid})
