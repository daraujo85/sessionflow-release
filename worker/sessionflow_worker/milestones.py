"""Sincroniza MARCOS do agente (arquivo no projeto) → coleção ``tasks``.

O agente (instruído por uma regra global no CLI) mantém um arquivo
``<work_dir>/.sessionflow/milestones.json`` com os marcos do trabalho e seus
status. O worker lê esse arquivo por sessão ativa e reflete na coleção
``tasks`` (que a Home já renderiza), de forma idempotente: só mexe no
``updated_at`` quando o marco MUDA (preserva a ordem "mais recentes").

Formato esperado do arquivo::

    {
      "milestones": [
        {"id": "resp-desktop", "title": "Responsividade desktop", "status": "done"},
        {"id": "web-push",     "title": "Web Push (VAPID)",       "status": "doing"}
      ]
    }

``status`` ∈ todo|doing|blocked|done (sinônimos: in_progress→doing). Marcos
sem ``id`` usam o título como chave. Arquivo ausente/ inválido = no-op.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("sessionflow_worker.milestones")

MILESTONES_REL_PATH = ".sessionflow/milestones.json"
TASKS_COLLECTION = "tasks"
MILESTONE_SOURCE = "milestone"

_VALID_STATES = {"todo", "doing", "blocked", "done"}
_STATE_ALIASES = {
    "in_progress": "doing",
    "in-progress": "doing",
    "wip": "doing",
    "pending": "todo",
    "backlog": "todo",
    "completed": "done",
    "complete": "done",
    "finished": "done",
    "blocked": "blocked",
}


def _parse_file(path: Path) -> list[dict[str, str]] | None:
    """Parseia um arquivo de marcos. None se ausente/inválido (nunca levanta)."""
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    items = data.get("milestones") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    out: list[dict[str, str]] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        title = str(m.get("title", "")).strip()
        if not title:
            continue
        mid = str(m.get("id") or title).strip()[:160]
        raw_state = str(m.get("status") or m.get("state") or "todo").strip().lower()
        state = _STATE_ALIASES.get(raw_state, raw_state)
        if state not in _VALID_STATES:
            state = "todo"
        out.append({"mid": mid, "title": title[:240], "state": state})
    return out


def read_milestones(
    work_dir: str, session_name: str, allow_shared: bool = True
) -> list[dict[str, str]] | None:
    """Lê os marcos da sessão, com namespacing por sessão.

    Prioriza ``.sessionflow/milestones.<session_name>.json`` (evita colisão
    quando várias sessões compartilham o mesmo ``work_dir``). Cai para o
    ``.sessionflow/milestones.json`` genérico só quando ``allow_shared`` (uso:
    diretório com uma única sessão / retrocompat). None se não houver arquivo.
    """
    if not work_dir:
        return None
    base = Path(work_dir).expanduser() / ".sessionflow"
    items = _parse_file(base / f"milestones.{session_name}.json")
    if items is not None:
        return items
    if allow_shared:
        return _parse_file(base / "milestones.json")
    return None


async def sync_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
    work_dir: str,
    session_name: str | None = None,
    allow_shared: bool = True,
    collection: str = TASKS_COLLECTION,
) -> int:
    """Reflete os marcos da sessão na coleção ``tasks``.

    Lê o arquivo namespaced ``.sessionflow/milestones.<session_name>.json``
    (fallback no genérico quando ``allow_shared``). Idempotente: upsert por
    (session_id, milestone_id); só bump de ``updated_at`` quando muda. Marcos
    ausentes (ou arquivo inexistente) são podados — limpa duplicatas antigas.
    Retorna nº de marcos.
    """
    items = read_milestones(work_dir, session_name or session_id, allow_shared)
    # Arquivo ausente → trata como vazio p/ PODAR tasks órfãs desta sessão.
    if items is None:
        items = []

    coll = db[collection]
    now = datetime.now(timezone.utc)
    seen: list[str] = []
    for m in items:
        seen.append(m["mid"])
        key = {"session_id": session_id, "milestone_id": m["mid"], "source": MILESTONE_SOURCE}
        existing = await coll.find_one(key, projection={"title": 1, "state": 1})
        changed = (
            existing is None
            or existing.get("title") != m["title"]
            or existing.get("state") != m["state"]
        )
        set_fields: dict[str, Any] = {**key, "title": m["title"], "state": m["state"]}
        if changed:
            set_fields["updated_at"] = now
        await coll.update_one(key, {"$set": set_fields}, upsert=True)

    # Poda marcos que saíram do arquivo (sem tocar em tasks de outras origens).
    await coll.delete_many(
        {
            "session_id": session_id,
            "source": MILESTONE_SOURCE,
            "milestone_id": {"$nin": seen},
        }
    )
    return len(items)
