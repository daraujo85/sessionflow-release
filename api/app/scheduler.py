"""Background loop: dispara comandos programados vencidos.

Roda dentro do processo da API (ver lifespan em ``app.main``), reaproveitando
o mesmo ``publish_command`` do ``POST /sessions/{id}/input`` manual — pro
worker/tmux não há diferença entre uma instrução digitada e uma programada.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings
from app.publishers.command_publisher import publish_command
from app.repositories.schedules_repo import SchedulesRepository
from app.repositories.sessions_repo import SessionsRepository

logger = logging.getLogger(__name__)


async def run_scheduler_forever(
    db: AsyncIOMotorDatabase, settings: Settings, poll_seconds: int | None = None
) -> None:
    """Nunca retorna (nem propaga exceção) — feito pra rodar como task solta."""
    interval = poll_seconds or settings.scheduler_poll_seconds
    schedules_repo = SchedulesRepository(db, settings.scheduled_commands_collection)
    sessions_repo = SessionsRepository(db, settings.sessions_collection)

    while True:
        try:
            await _tick(schedules_repo, sessions_repo, settings)
        except Exception:  # noqa: BLE001 - loop de background não pode morrer
            logger.exception("scheduler: tick falhou")
        await asyncio.sleep(interval)


async def _tick(
    schedules_repo: SchedulesRepository,
    sessions_repo: SessionsRepository,
    settings: Settings,
) -> None:
    due = await schedules_repo.due(datetime.now(UTC))
    for doc in due:
        error: str | None = None
        session = await sessions_repo.get_session(doc["session_id"])
        if session is None:
            error = "sessão não encontrada (foi excluída?)"
        else:
            try:
                await publish_command(
                    settings,
                    type="input",
                    payload={
                        "name": session["tmux_name"],
                        "text": doc["text"],
                        "enter": True,
                    },
                    host_id=session.get("host_id"),
                )
            except Exception as exc:  # noqa: BLE001 - registra e segue os demais
                error = str(exc)
                logger.warning("scheduler: comando %s falhou: %s", doc["_id"], exc)
        await schedules_repo.mark_ran(doc["_id"], doc["interval_seconds"], error)
