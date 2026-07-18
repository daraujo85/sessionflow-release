"""Repository for scheduled recurring commands (comandos programados).

Each doc represents "envie este texto pro terminal desta sessão a cada N
segundos", com pausa (``enabled``) e histórico do último disparo. Um loop
em background (``app.scheduler``) varre os vencidos e os executa via o
mesmo ``publish_command``/`input` usado pelo composer manual.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase


class SchedulesRepository:
    def __init__(
        self, db: AsyncIOMotorDatabase, collection_name: str = "scheduled_commands"
    ) -> None:
        self._collection = db[collection_name]

    async def create(
        self, session_id: str, text: str, interval_seconds: int
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc: dict[str, Any] = {
            "session_id": session_id,
            "text": text,
            "interval_seconds": interval_seconds,
            "enabled": True,
            "next_run_at": now + timedelta(seconds=interval_seconds),
            "last_run_at": None,
            "last_error": None,
            "created_at": now,
        }
        res = await self._collection.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        cursor = self._collection.find({"session_id": session_id}).sort("created_at", 1)
        return [doc async for doc in cursor]

    async def get(self, schedule_id: str) -> dict[str, Any] | None:
        try:
            oid = ObjectId(schedule_id)
        except (InvalidId, TypeError):
            return None
        return await self._collection.find_one({"_id": oid})

    async def update(
        self,
        schedule_id: str,
        *,
        text: str | None = None,
        interval_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Edita campos informados; ``None`` = "não mexe nesse campo".

        Reagenda ``next_run_at`` a partir de AGORA quando o intervalo muda ou
        quando o comando é RETOMADO (``enabled`` False→True) — senão um
        comando pausado há dias disparia na hora ao ser reativado, e mudar o
        intervalo de 1h pra 5min não deveria herdar o "vencimento" antigo.
        """
        try:
            oid = ObjectId(schedule_id)
        except (InvalidId, TypeError):
            return None

        current = await self._collection.find_one({"_id": oid})
        if current is None:
            return None

        fields: dict[str, Any] = {}
        if text is not None:
            fields["text"] = text
        if interval_seconds is not None:
            fields["interval_seconds"] = interval_seconds
        if enabled is not None:
            fields["enabled"] = enabled

        resuming = enabled is True and not current.get("enabled", True)
        if interval_seconds is not None or resuming:
            effective_interval = interval_seconds or current["interval_seconds"]
            fields["next_run_at"] = datetime.now(UTC) + timedelta(
                seconds=effective_interval
            )

        if not fields:
            return current

        await self._collection.update_one({"_id": oid}, {"$set": fields})
        return await self._collection.find_one({"_id": oid})

    async def delete(self, schedule_id: str) -> bool:
        try:
            oid = ObjectId(schedule_id)
        except (InvalidId, TypeError):
            return False
        res = await self._collection.delete_one({"_id": oid})
        return res.deleted_count > 0

    async def due(self, now: datetime) -> list[dict[str, Any]]:
        """Comandos habilitados cujo ``next_run_at`` já passou."""
        cursor = self._collection.find({"enabled": True, "next_run_at": {"$lte": now}})
        return [doc async for doc in cursor]

    async def mark_ran(
        self, schedule_id: ObjectId, interval_seconds: int, error: str | None
    ) -> None:
        """Registra o disparo (sucesso ou erro) e agenda o próximo."""
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": schedule_id},
            {
                "$set": {
                    "last_run_at": now,
                    "next_run_at": now + timedelta(seconds=interval_seconds),
                    "last_error": error,
                }
            },
        )
