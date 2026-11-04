"""Background loop: dispara comandos programados vencidos.

Roda dentro do processo da API (ver lifespan em ``app.main``), reaproveitando
o mesmo ``publish_command`` do ``POST /sessions/{id}/input`` manual — pro
worker/tmux não há diferença entre uma instrução digitada e uma programada.

Esse arquivo tem QUATRO mecanismos independentes que só compartilham o loop:
- ``_tick``: comandos programados que o USUÁRIO cria (coleção
  ``scheduled_commands`` — painel "Comandos programados" por sessão).
- ``_milestones_tick``: revisão automática de milestones a cada
  ``milestones_refresh_interval_seconds``, sem nenhuma relação com a coleção
  acima (nem aparece nela) — substitui o antigo botão manual "Atualizar todos
  os marcos" do Início, agora 100% backend e sem precisar de clique.
- ``_ephemeral_tick``: apaga sessões efêmeras (UI "Rápida") há mais de 24h
  sem atividade (delega pro mesmo endpoint ``DELETE /sessions/{id}/purge``,
  que mata tmux, apaga doc e a pasta scratch no host).
- ``_orphan_tasks_tick``: apaga marcos (tasks ``source=milestone``) cuja
  sessão não existe mais — o worker só sincroniza sessões vivas, então essas
  ficavam "presas" pra sempre na Home.
- ``_command_bridges_tick``: mata as PONTES DE COMANDO mortas — a sessão
  "comando" que faz a ponte com um host (`sf command <host>`). Voltaátil:
  morre por TTL ocioso, em cascata quando a sessão dona acaba, e nunca passa
  de uma por host.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

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

# TTL das sessões efêmeras (UI "Rápida"). Worker apaga tmux + doc + scratch
# após esse tempo sem atividade (qualquer update do doc zera o relógio —
# não é "sessão rodando há X tempo", é "sessão abandonada há X tempo").
EPHEMERAL_TTL = timedelta(hours=24)


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
        try:
            await _ephemeral_tick(db, sessions_repo, settings)
        except Exception:  # noqa: BLE001 - loop de background não pode morrer
            logger.exception("scheduler: ephemeral tick falhou")
        try:
            await _command_bridges_tick(db, sessions_repo, settings)
        except Exception:  # noqa: BLE001 - loop de background não pode morrer
            logger.exception("scheduler: command bridges tick falhou")
        try:
            await _orphan_tasks_tick(db, settings)
        except Exception:  # noqa: BLE001 - loop de background não pode morrer
            logger.exception("scheduler: orphan tasks tick falhou")
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
        except Exception as exc:  # noqa: BLE001 - registra e segue os demais
            logger.warning(
                "scheduler: revisão de milestones de %s falhou: %s", tmux_name, exc
            )
            continue
        await sessions_repo.mark_milestones_refreshed(doc["_id"], now)


async def _ephemeral_tick(
    db: AsyncIOMotorDatabase,
    sessions_repo: SessionsRepository,
    settings: Settings,
) -> None:
    """Apaga sessões efêmeras (UI "Rápida") abandonadas há mais de 24h.

    Mesma mecânica do ``DELETE /sessions/{id}/purge`` (mata tmux, apaga doc +
    dados relacionados e, no worker, remove a pasta scratch do host quando o
    payload carrega ``ephemeral=True`` + ``work_dir``). Reaproveitamos o
    repositório em vez de chamar o endpoint HTTP — evita custo de JWT/auth e
    deixa o fluxo uniforme com os outros ticks.
    """
    cutoff = datetime.now(UTC) - EPHEMERAL_TTL
    due = await sessions_repo.due_ephemeral(cutoff)
    if not due:
        return
    purged = 0
    for doc in due:
        session_id = str(doc["_id"])
        tmux_name = doc.get("tmux_name") or ""
        host_id = doc.get("host_id")
        try:
            await sessions_repo.delete_session(session_id)
            payload: dict[str, Any] = {"name": tmux_name, "ephemeral": True}
            if doc.get("work_dir"):
                payload["work_dir"] = doc["work_dir"]
            await publish_command(
                settings, type="delete", payload=payload, host_id=host_id
            )
            purged += 1
        except Exception as exc:  # noqa: BLE001 - registra e segue os demais
            logger.warning(
                "scheduler: purge de efêmera %s falhou: %s", tmux_name, exc
            )
            continue
    if purged:
        logger.info("scheduler: %d sessão(ões) efêmera(s) purgadas", purged)


async def _command_bridges_tick(
    db: AsyncIOMotorDatabase,
    sessions_repo: SessionsRepository,
    settings: Settings,
) -> None:
    """Mata as PONTES DE COMANDO mortas.

    A ponte é a sessão "comando" de um host-alvo: ``sf command <host>`` reusa
    sempre a mesma (uma por host, não uma por invocação). Ela é VOLÁTIL:

    - **TTL ocioso**: ninguém usa há ``command_bridge_ttl_seconds`` → morre.
    - **Cascata**: a sessão DONA da ponte acabou → a ponte morre junto.
    - **Dedupe**: mais de uma ponte viva no mesmo host → as menos ativas morrem.

    ``keep_alive`` (serviço de longa duração, ex.: relay) fica de fora das
    três regras e nunca é reusada como ponte pelo CLI.

    Purge = apaga o doc + publica ``delete`` pro host. ⚠️ O payload NÃO leva
    ``ephemeral``/``work_dir``: o ``work_dir`` da ponte é o HOME do host e o
    worker removeria a pasta inteira (``shutil.rmtree``) — só manda quando a
    sessão é efêmera de verdade.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.command_bridge_ttl_seconds)

    # 1) TTL ocioso.
    due: list[dict[str, Any]] = await sessions_repo.due_command_bridges(cutoff)

    # 2) Cascata: dona que não está mais viva (doc sumiu ou status final).
    alive = await sessions_repo.alive_session_names(ACTIVE_STATUSES)
    for doc in await sessions_repo.owned_command_bridges():
        if doc.get("owner") not in alive:
            due.append(doc)

    # 3) Dedupe: uma ponte por host. `command_bridges` vem da mais recente pra
    # mais antiga — a mais recente sobrevive, as outras morrem.
    seen_hosts: set[str] = set()
    for doc in await sessions_repo.command_bridges():
        if doc.get("keep_alive"):
            continue
        if doc.get("status") not in ACTIVE_STATUSES:
            continue
        host = str(doc.get("host_id") or "")
        if host in seen_hosts:
            due.append(doc)
        else:
            seen_hosts.add(host)

    purged = 0
    seen_ids: set[str] = set()
    for doc in due:
        session_id = str(doc["_id"])
        if session_id in seen_ids:
            continue
        seen_ids.add(session_id)
        tmux_name = doc.get("tmux_name") or ""
        try:
            await sessions_repo.delete_session(session_id)
            await publish_command(
                settings,
                type="delete",
                payload={"name": tmux_name},
                host_id=doc.get("host_id"),
            )
            purged += 1
        except Exception as exc:  # noqa: BLE001 - registra e segue as demais
            logger.warning("scheduler: purge de ponte %s falhou: %s", tmux_name, exc)
    if purged:
        logger.info("scheduler: %d ponte(s) de comando purgada(s)", purged)


async def _orphan_tasks_tick(db: AsyncIOMotorDatabase, settings: Settings) -> None:
    """Apaga marcos cuja sessão dona sumiu (``tasks.session_id`` = ``tmux_name``).

    Só toca ``source=milestone`` (as tasks espelhadas do arquivo do agente) —
    sem sessão, nenhum worker volta a sincronizá-las, então nunca sairiam do
    painel. Sessão parada (doc ainda existe) NÃO entra: o arquivo pode voltar a
    ser sincronizado se ela for retomada.
    """
    alive = await db[settings.sessions_collection].distinct("tmux_name")
    if not alive:
        # Coleção vazia/indisponível: não arrisca apagar o painel inteiro.
        return
    res = await db[settings.tasks_collection].delete_many(
        {"source": "milestone", "session_id": {"$nin": alive}}
    )
    if res.deleted_count:
        logger.info("scheduler: %d marco(s) órfão(s) apagados", res.deleted_count)
