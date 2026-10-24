"""Cutuca filhos de ``sf delegate`` que travaram/ficaram ociosos sem handoff.

Casos reais: o agente parou num ``Connection dropped (ECONNRESET) attempt
9/10`` e ficou parado; ou terminou o bloco (activity ``Concluído``, prompt
ocioso) sem executar a tarefa nem escrever o handoff. O ``handoff_notify`` só
age quando o filho TERMINA — este módulo cobre o filho vivo e parado.

Candidatos: sessões deste host com ``parent``, sem ``parent_notified_at``, sem
handoff desta rodada, status não-final e com mais de ``MIN_AGE``. Para cada um,
:func:`decide` (pura) olha o doc + a cauda da tela (doc ``session_screen`` que
o capture loop já mantém — nada de ``capture-pane`` extra) e responde:

- ``nudge``: publica ``input`` na PRÓPRIA sessão filha pedindo pra continuar;
  no máx. ``SF_IDLE_MAX_NUDGES`` com backoff ``NUDGE_INTERVALS``.
- ``escalate`` (1x): esgotou as cutucadas, ou o filho está em ``Aguardando
  sua decisão`` há ``DECISION_ESCALATE`` — avisa o PAI pra decidir/redelegar.

Estado em ``watchdog: {nudges, last_nudge_at, escalated_at}`` no doc do filho;
zera quando há atividade real ``RESET_AFTER`` depois da última ação.
``SF_IDLE_WATCHDOG=0`` desliga.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker.handoff_notify import (
    MAX_AGE,
    SESSIONS_COLLECTION,
    Publish,
    _parent_route,
    _utc,
    find_handoff,
)
from sessionflow_worker.output_capture import DEFAULT_SCREEN_COLLECTION, strip_ansi
from sessionflow_worker.rabbit import commands_queue_name

logger = logging.getLogger("sessionflow_worker.idle_watchdog")

Action = Literal["none", "nudge", "escalate"]

IDLE_WATCHDOG_INTERVAL = 60.0
# Filho recém-criado ainda está subindo/lendo o prompt.
MIN_AGE = timedelta(minutes=3)
# Espera antes da cutucada N+1 (índice = cutucadas já feitas - 1); a última
# vale também como espera antes de escalar.
NUDGE_INTERVALS = (timedelta(minutes=5), timedelta(minutes=10), timedelta(minutes=20))
# Atividade real (tela mudou) este tempo depois da última ação = progrediu,
# zera o estado. Margem cobre o próprio ``input`` (que bate last_activity_at).
RESET_AFTER = timedelta(minutes=2)
# Erro na tela só conta com a tela parada há este tempo: o agente pode estar
# no meio de um retry, e o auto-continue da discovery age antes (~6s).
ERROR_QUIET = timedelta(minutes=2)
DECISION_ESCALATE = timedelta(minutes=5)
FINAL_STATUSES = frozenset({"stopped", "detached", "error", "completed"})
ACTIVITY_IDLE = "Concluído"
ACTIVITY_DECISION = "Aguardando sua decisão"
TAIL_LINES = 15

_STUCK_ERROR_RE = re.compile(
    r"ECONNRESET|Connection dropped|API Error|overloaded|rate limit|\b529\b|timed out",
    re.IGNORECASE,
)


def enabled() -> bool:
    return os.environ.get("SF_IDLE_WATCHDOG", "1").strip().lower() not in ("0", "false", "no")


def _idle_secs() -> float:
    try:
        return float(os.environ.get("SF_IDLE_SECS", "600"))
    except ValueError:
        return 600.0


def _max_nudges() -> int:
    try:
        return max(0, int(os.environ.get("SF_IDLE_MAX_NUDGES", "3")))
    except ValueError:
        return 3


def clean_tail(text: str, lines: int = TAIL_LINES) -> str:
    """Últimas ``lines`` linhas não-vazias da tela, sem ANSI."""
    clean = [ln for ln in strip_ansi(text or "").split("\n") if ln.strip()]
    return "\n".join(clean[-lines:])


def _made_progress(doc: dict[str, Any], tail: str) -> bool:
    """Tela mudou E o que ficou nela não é sinal de travado. Retry de rede
    ("Connection dropped ... attempt N/10") mexe a tela por minutos sem
    progredir — não pode zerar o contador, senão cutuca pra sempre."""
    return not _STUCK_ERROR_RE.search(tail or "") and doc.get("activity") != ACTIVITY_IDLE


def _state(doc: dict[str, Any], tail: str) -> dict[str, Any]:
    """Estado ``watchdog`` efetivo — já zerado se houve progresso real
    ``RESET_AFTER`` depois da última cutucada/escalada."""
    raw = doc.get("watchdog") or {}
    state = {
        "nudges": int(raw.get("nudges") or 0),
        "last_nudge_at": _utc(raw.get("last_nudge_at")),
        "escalated_at": _utc(raw.get("escalated_at")),
    }
    marks = [m for m in (state["last_nudge_at"], state["escalated_at"]) if m]
    activity = _utc(doc.get("last_activity_at"))
    if (
        marks and activity and activity > max(marks) + RESET_AFTER
        and _made_progress(doc, tail)
    ):
        state = {"nudges": 0, "last_nudge_at": None, "escalated_at": None}
    return state


def stuck_reason(doc: dict[str, Any], tail: str, now: datetime) -> str | None:
    """Por que o filho parece travado (texto p/ o pai), ou None."""
    activity = doc.get("activity") or ""
    last = _utc(doc.get("last_activity_at")) or _utc(doc.get("created_at")) or now
    quiet = now - last
    if activity == ACTIVITY_DECISION:
        return f"aguardando decisão há {int(quiet.total_seconds() // 60)} min"
    m = _STUCK_ERROR_RE.search(tail or "")
    if m and quiet >= ERROR_QUIET:
        return f"erro na tela ({m.group(0)})"
    if activity == ACTIVITY_IDLE:
        return "prompt ocioso (Concluído) sem handoff"
    if quiet >= timedelta(seconds=_idle_secs()):
        return f"sem atividade há {int(quiet.total_seconds() // 60)} min"
    return None


def decide(
    doc: dict[str, Any], screen_tail: str, now: datetime, *, has_handoff: bool = False
) -> Action:
    """Decisão pura do watchdog para um filho delegado."""
    created = _utc(doc.get("created_at"))
    if (
        not doc.get("parent")
        or doc.get("parent_notified_at")
        or has_handoff
        or doc.get("status") in FINAL_STATUSES
        or created is None
        or now - created < MIN_AGE
    ):
        return "none"
    reason = stuck_reason(doc, screen_tail, now)
    if reason is None:
        return "none"
    state = _state(doc, screen_tail)
    if state["escalated_at"]:
        return "none"
    last = _utc(doc.get("last_activity_at")) or created
    if doc.get("activity") == ACTIVITY_DECISION:
        # Responder um menu com texto seria escolher por ele: só o pai decide.
        return "escalate" if now - last >= DECISION_ESCALATE else "none"
    max_nudges = _max_nudges()
    nudges = state["nudges"]
    if nudges:
        wait = NUDGE_INTERVALS[min(nudges, len(NUDGE_INTERVALS)) - 1]
        if now - state["last_nudge_at"] < wait:
            return "none"
    return "nudge" if nudges < max_nudges else "escalate"


def build_nudge(name: str) -> str:
    return (
        "Watchdog: sua tarefa delegada ainda não tem handoff. Continue de onde "
        f"parou; se já terminou, escreva o handoff em .sessionflow/handoff/{name}.handoff.json "
        "e avise o pai via sf send."
    )


def build_escalation(name: str, host_id: str, nudges: int, reason: str) -> str:
    return (
        f"⚠️ TRAVADO {name} (host {host_id}): {nudges} cutucadas sem progresso / {reason}. "
        "Considere redelegar: sf delegate --host auto-remote ..."
    )


def _has_handoff(doc: dict[str, Any], name: str, created: datetime | None) -> bool:
    path = find_handoff(doc.get("work_dir") or "", [name, doc.get("tmux_name") or ""])
    if path is None:
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return created is None or mtime >= created  # anterior = rodada antiga


async def _screen_tail(db: AsyncIOMotorDatabase, tmux_name: str) -> str:
    doc = await db[DEFAULT_SCREEN_COLLECTION].find_one(
        {"tmux_name": tmux_name}, projection={"text": 1}
    )
    return clean_tail((doc or {}).get("text") or "")


def _input(queue_host: str, name: str, text: str, now: datetime) -> tuple[str, dict[str, Any]]:
    return commands_queue_name(queue_host), {
        "command_id": str(uuid.uuid4()),
        "type": "input",
        "payload": {"name": name, "text": text, "enter": True},
        "requested_at": now.isoformat(),
    }


async def watchdog_once(
    db: AsyncIOMotorDatabase,
    publish: Publish,
    host_id: str,
    now: datetime | None = None,
    collection: str = SESSIONS_COLLECTION,
) -> list[tuple[str, Action]]:
    """Uma passada. Retorna ``(tmux_name, ação)`` das sessões em que agiu."""
    if not enabled():
        return []
    now = now or datetime.now(timezone.utc)
    coll = db[collection]
    children = await coll.find(
        {
            "parent": {"$nin": [None, ""]},
            "host_id": host_id,
            "parent_notified_at": None,
            "status": {"$nin": sorted(FINAL_STATUSES)},
            "created_at": {"$gte": now - MAX_AGE},
        },
        projection={
            "tmux_name": 1, "display_name": 1, "parent": 1, "work_dir": 1, "status": 1,
            "created_at": 1, "parent_notified_at": 1, "activity": 1,
            "last_activity_at": 1, "watchdog": 1,
        },
    ).to_list(length=200)

    acted: list[tuple[str, Action]] = []
    for child in children:
        tmux_name = child.get("tmux_name")
        created = _utc(child.get("created_at"))
        if not tmux_name or created is None or now - created < MIN_AGE:
            continue
        name = child.get("display_name") or tmux_name
        if _has_handoff(child, name, created):
            continue  # handoff_notify cuida do aviso
        tail = await _screen_tail(db, tmux_name)
        action = decide(child, tail, now)
        if action == "none":
            continue
        state = _state(child, tail)
        if action == "nudge":
            await publish(*_input(host_id, tmux_name, build_nudge(name), now))
            state = {**state, "nudges": state["nudges"] + 1, "last_nudge_at": now}
            logger.info("watchdog cutucou %r (#%d)", tmux_name, state["nudges"])
        else:
            parent = child["parent"]
            reason = stuck_reason(child, tail, now) or "?"
            parent_host = await _parent_route(db, parent, collection)
            if parent_host is None:
                logger.info("pai %r de %r ausente/parado; escalada descartada", parent, tmux_name)
            else:
                text = build_escalation(name, host_id, state["nudges"], reason)
                await publish(*_input(parent_host, parent, text, now))
                logger.info("watchdog escalou %r para o pai %r (%s)", tmux_name, parent, reason)
            state = {**state, "escalated_at": now}
        await coll.update_one({"_id": child["_id"]}, {"$set": {"watchdog": state}})
        acted.append((tmux_name, action))
    return acted


async def idle_watchdog_loop(
    db: AsyncIOMotorDatabase,
    publish: Publish,
    host_id: str,
    interval: float = IDLE_WATCHDOG_INTERVAL,
) -> None:
    while True:
        try:
            await watchdog_once(db, publish, host_id)
        except Exception:  # noqa: BLE001 - watchdog nunca derruba o daemon
            logger.debug("idle_watchdog ciclo falhou", exc_info=True)
        await asyncio.sleep(interval)
