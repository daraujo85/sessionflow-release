"""Background loop: dispara comandos programados vencidos.

Roda dentro do processo da API (ver lifespan em ``app.main``), reaproveitando
o mesmo ``publish_command`` do ``POST /sessions/{id}/input`` manual — pro
worker/tmux não há diferença entre uma instrução digitada e uma programada.

Esse arquivo tem DOIS mecanismos independentes que só compartilham o loop:
- ``_tick``: comandos programados que o USUÁRIO cria (coleção
  ``scheduled_commands`` — painel "Comandos programados" por sessão).
- ``_milestones_tick``: revisão automática de milestones a cada
  ``milestones_refresh_interval_seconds``, sem nenhuma relação com a coleção
  acima (nem aparece nela) — substitui o antigo botão manual "Atualizar todos
  os marcos" do Início, agora 100% backend e sem precisar de clique.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings
from app.publishers.command_publisher import publish_command
from app.repositories.schedules_repo import SchedulesRepository
from app.repositories.sessions_repo import SessionsRepository
from app.routers.settings import SETTINGS_ID, milestones_refresh_instruction

logger = logging.getLogger(__name__)

# Mesmas status considerados "ativos" pelo front (ver ACTIVE_STATUSES em
# inicio.component.ts) — quem está fora disso não trabalha, não faz sentido
# revisar milestones.
ACTIVE_STATUSES = ["running", "waiting_input"]


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
        try:
            await _milestones_tick(db, sessions_repo, settings)
        except Exception:  # noqa: BLE001 - loop de background não pode morrer
            logger.exception("scheduler: milestones tick falhou")
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


async def _milestones_tick(
    db: AsyncIOMotorDatabase,
    sessions_repo: SessionsRepository,
    settings: Settings,
) -> None:
    """Revisa milestones das sessões ativas a cada `interval` — sem botão.

    Independente de `_tick`/`scheduled_commands`: nem lê nem escreve aquela
    coleção. Gated pelo mesmo `milestones_auto` do settings global (o mesmo
    toggle que já gate `POST /instruct-milestones`).
    """
    cfg = await db[settings.app_settings_collection].find_one({"_id": SETTINGS_ID})
    if cfg is not None and not cfg.get("milestones_auto", True):
        return

    interval = settings.milestones_refresh_interval_seconds
    cutoff = datetime.now(UTC) - timedelta(seconds=interval)
    due = await sessions_repo.due_for_milestones_refresh(cutoff, ACTIVE_STATUSES)
    now = datetime.now(UTC)
    for doc in due:
        tmux_name = doc.get("tmux_name")
        if not tmux_name:
            continue
        try:
            await publish_command(
                settings,
                type="input",
                payload={
                    "name": tmux_name,
                    "text": milestones_refresh_instruction(tmux_name),
                    "enter": True,
                },
                host_id=doc.get("host_id"),
            )
        except Exception as exc:  # noqa: BLE001 - registra e segue as demais
            logger.warning(
                "scheduler: revisão de milestones de %s falhou: %s", tmux_name, exc
            )
            continue
        await sessions_repo.mark_milestones_refreshed(doc["_id"], now)
