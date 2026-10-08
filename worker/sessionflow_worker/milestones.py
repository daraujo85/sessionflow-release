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


def _parse_description(path: Path) -> str | None:
    """Campo top-level ``description`` do arquivo de marcos (ou ``None``).

    Descrição BREVE e "viva" do que se trata a sessão, escrita pela própria IA
    da sessão e atualizada quando o foco muda — o app mostra junto do nome.
    """
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    desc = data.get("description")
    if not isinstance(desc, str):
        return None
    desc = desc.strip()
    return desc[:280] if desc else None


def read_description(
    work_dir: str, session_name: str, allow_shared: bool = True
) -> str | None:
    """Lê a descrição da sessão do arquivo namespaced (mesma lógica de path)."""
    if not work_dir:
        return None
    base = Path(work_dir).expanduser() / ".sessionflow"
    desc = _parse_description(base / f"milestones.{session_name}.json")
    if desc is not None:
        return desc
    if allow_shared:
        return _parse_description(base / "milestones.json")
    return None


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


def remove_milestone(work_dir: str, session_name: str, milestone_id: str) -> bool:
    """Remove o marco ``milestone_id`` do arquivo namespaced da sessão.

    Usa a MESMA lógica de path do :func:`read_milestones`
    (``.sessionflow/milestones.<session_name>.json``, expande ``~``). Lê o
    JSON ({"milestones": [{id, title, status}, ...]}), descarta a entrada cujo
    ``id`` (fallback no ``title``, como no parse) bate com ``milestone_id`` e
    regrava o arquivo na mesma forma (json identado, utf-8).

    Best-effort/tolerante: arquivo ausente/inválido → False, nunca levanta.
    Retorna True só quando algo foi removido.
    """
    if not work_dir or not milestone_id:
        return False
    path = Path(work_dir).expanduser() / ".sessionflow" / f"milestones.{session_name}.json"
    try:
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    if not isinstance(data, dict):
        return False
    items = data.get("milestones")
    if not isinstance(items, list):
        return False

    target = str(milestone_id).strip()
    kept: list[Any] = []
    removed = False
    for m in items:
        if isinstance(m, dict):
            title = str(m.get("title", "")).strip()
            mid = str(m.get("id") or title).strip()
            if mid == target:
                removed = True
                continue
        kept.append(m)

    if not removed:
        return False

    data["milestones"] = kept
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        return False
    return True


async def sync_session(
    db: AsyncIOMotorDatabase,
    session_id: str,
    work_dir: str,
    session_name: str | None = None,
    allow_shared: bool = True,
    collection: str = TASKS_COLLECTION,
) -> list[dict[str, str]]:
    """Reflete os marcos da sessão na coleção ``tasks``.

    Lê o arquivo namespaced ``.sessionflow/milestones.<session_name>.json``
    (fallback no genérico quando ``allow_shared``). Idempotente: upsert por
    (session_id, milestone_id); só bump de ``updated_at`` quando muda. Marcos
    ausentes (ou arquivo inexistente) são podados — limpa duplicatas antigas.

    Retorna a lista de marcos que ACABARAM de virar "done" nesta passada (só os
    que existiam antes num estado diferente) — o caller usa p/ emitir o evento de
    "tarefa concluída" (som de vitória + destaque). Não dispara na 1ª aparição
    já-done (evita falsa vitória ao importar o arquivo).
    """
    items = read_milestones(work_dir, session_name or session_id, allow_shared)
    # Arquivo ausente → trata como vazio p/ PODAR tasks órfãs desta sessão.
    if items is None:
        items = []

    # Descrição "viva" da sessão (campo top-level do mesmo arquivo, escrito
    # pela IA da sessão) → espelha no doc da sessão (ai_description) pro app
    # mostrar junto do nome. Só grava quando muda; ausente não apaga (a IA
    # pode ainda não ter criado o campo).
    desc = read_description(work_dir, session_name or session_id, allow_shared)
    if desc is not None:
        try:
            await db["sessions"].update_one(
                {"tmux_name": session_name or session_id, "ai_description": {"$ne": desc}},
                {"$set": {"ai_description": desc}},
            )
        except Exception:  # noqa: BLE001 - best-effort, nunca trava o sync
            pass

    coll = db[collection]
    now = datetime.now(timezone.utc)
    seen: list[str] = []
    newly_done: list[dict[str, str]] = []
    for m in items:
        seen.append(m["mid"])
        key = {"session_id": session_id, "milestone_id": m["mid"], "source": MILESTONE_SOURCE}
        existing = await coll.find_one(key, projection={"title": 1, "state": 1})
        changed = (
            existing is None
            or existing.get("title") != m["title"]
            or existing.get("state") != m["state"]
        )
        if (
            existing is not None
            and existing.get("state") != "done"
            and m["state"] == "done"
        ):
            newly_done.append({"mid": m["mid"], "title": m["title"]})
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
    return newly_done
