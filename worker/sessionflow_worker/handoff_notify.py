"""Avisa a sessão PAI quando um filho de ``sf delegate`` termina.

O ``sf delegate`` grava ``parent`` (tmux_name de quem delegou) no doc do filho
e instrui o agente filho a mandar ``sf send <pai> "✅ HANDOFF ..."`` no fim —
mas isso depende do agente obedecer. Este módulo é a rede de segurança: o
worker do host do FILHO observa o fim do trabalho e injeta o aviso no pai.

Fim do trabalho = ``<work_dir>/.sessionflow/handoff/<nome>.handoff.json``
escrito depois da criação da sessão (status vem do JSON), ou sessão encerrada
(stopped/detached/error) sem handoff. O aviso é publicado como comando
``input`` na fila do host do PAI (pode ser outro host — delegação remota).

Idempotente: marca ``parent_notified_at`` no doc do filho. Se o próprio agente
já avisou (texto ``HANDOFF <nome>`` capturado no output do pai), só marca.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker.rabbit import commands_queue_name

logger = logging.getLogger("sessionflow_worker.handoff_notify")

SESSIONS_COLLECTION = "sessions"
OUTPUT_COLLECTION = "session_output"
HANDOFF_NOTIFY_INTERVAL = 15.0
# Espera após o handoff aparecer: dá tempo do agente mandar o próprio
# `sf send` (aí o worker só marca, sem duplicar).
GRACE = timedelta(seconds=60)
# Só filhos recentes: no 1º deploy não dispara aviso p/ delegações antigas.
MAX_AGE = timedelta(hours=48)
END_STATUSES = frozenset({"stopped", "detached", "error"})
# "Sessão encerrada sem handoff" só vale p/ filho com essa idade mínima: a
# discovery às vezes marca ``stopped`` uma sessão recém-criada por segundos.
MIN_AGE_NO_HANDOFF = timedelta(minutes=2)

Publish = Callable[[str, dict[str, Any]], Awaitable[None]]
HasSession = Callable[[str], bool]


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def find_handoff(work_dir: str, names: list[str]) -> Path | None:
    """Primeiro ``<nome>.handoff.json`` existente entre os nomes da sessão.

    O ``sf`` usa o nome cru (``display_name``); o worker slugifica o tmux_name
    — tenta os dois.
    """
    base = Path(work_dir).expanduser() / ".sessionflow" / "handoff"
    for name in dict.fromkeys(n for n in names if n):
        path = base / f"{name}.handoff.json"
        if path.is_file():
            return path
    return None


def read_status(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "ilegível"
    status = data.get("status") if isinstance(data, dict) else None
    return str(status) if status else "?"


def build_notice(name: str, status: str) -> str:
    return f"✅ HANDOFF {name} status={status} (aviso do worker)"


async def _parent_route(
    db: AsyncIOMotorDatabase, parent: str, collection: str
) -> str | None:
    """host_id da sessão pai ATIVA, ou None. Pai parado não recebe aviso: o
    ``input`` numa sessão parada a RESSUSCITA (``_handle_input``)."""
    docs = await db[collection].find(
        {"tmux_name": parent}, projection={"host_id": 1, "status": 1}
    ).to_list(length=10)
    for doc in docs:
        if doc.get("host_id") and doc.get("status") != "stopped":
            return doc["host_id"]
    return None


async def _parent_already_told(
    db: AsyncIOMotorDatabase, parent: str, name: str, since: datetime
) -> bool:
    doc = await db[OUTPUT_COLLECTION].find_one(
        {
            "tmux_name": parent,
            "text": {"$regex": rf"HANDOFF {re.escape(name)}\b"},
            "at": {"$gte": since},
        },
        projection={"_id": 1},
    )
    return doc is not None


async def notify_once(
    db: AsyncIOMotorDatabase,
    publish: Publish,
    host_id: str,
    now: datetime | None = None,
    collection: str = SESSIONS_COLLECTION,
    has_session: HasSession | None = None,
) -> list[str]:
    """Uma passada: avisa os pais dos filhos que terminaram. Retorna os filhos
    marcados como notificados (avisados agora ou já avisados pelo agente)."""
    now = now or datetime.now(timezone.utc)
    coll = db[collection]
    children = await coll.find(
        {
            "parent": {"$nin": [None, ""]},
            "host_id": host_id,
            "parent_notified_at": None,
            "created_at": {"$gte": now - MAX_AGE},
        },
        projection={
            "tmux_name": 1, "display_name": 1, "parent": 1, "work_dir": 1,
            "status": 1, "created_at": 1, "parent_notified_at": 1,
        },
    ).to_list(length=200)

    marked: list[str] = []
    for child in children:
        tmux_name = child.get("tmux_name")
        parent = child.get("parent")
        created = _utc(child.get("created_at"))
        if not tmux_name or not parent or child.get("parent_notified_at") or created is None:
            continue
        name = child.get("display_name") or tmux_name

        status = None
        path = find_handoff(child.get("work_dir") or "", [name, tmux_name])
        if path is not None:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < created:
                path = None  # handoff de uma rodada anterior com o mesmo nome
            elif now - mtime < GRACE:
                continue
            else:
                status = read_status(path)
        if status is None:
            ended = child.get("status")
            if ended not in END_STATUSES or now - created < MIN_AGE_NO_HANDOFF:
                continue
            # "stopped" com tmux vivo aqui = doc defasado, não fim de verdade.
            if ended == "stopped" and has_session is not None and has_session(tmux_name):
                continue
            status = f"sem-handoff (sessão {child.get('status')})"

        if not await _parent_already_told(db, parent, name, created):
            parent_host = await _parent_route(db, parent, collection)
            if parent_host is None:
                logger.info("pai %r de %r ausente/parado; aviso descartado", parent, tmux_name)
            else:
                await publish(
                    commands_queue_name(parent_host),
                    {
                        "command_id": str(uuid.uuid4()),
                        "type": "input",
                        "payload": {"name": parent, "text": build_notice(name, status), "enter": True},
                        "requested_at": now.isoformat(),
                    },
                )
                logger.info("avisou pai %r: %r terminou (status=%s)", parent, tmux_name, status)
        await coll.update_one(
            {"_id": child["_id"]}, {"$set": {"parent_notified_at": now}}
        )
        marked.append(tmux_name)
    return marked


async def handoff_notify_loop(
    db: AsyncIOMotorDatabase,
    publish: Publish,
    host_id: str,
    interval: float = HANDOFF_NOTIFY_INTERVAL,
    has_session: HasSession | None = None,
) -> None:
    while True:
        try:
            await notify_once(db, publish, host_id, has_session=has_session)
        except Exception:  # noqa: BLE001 - aviso nunca derruba o daemon
            logger.debug("handoff_notify ciclo falhou", exc_info=True)
        await asyncio.sleep(interval)
