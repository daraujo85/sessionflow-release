"""Global-mode voice routing (spec §10, §25): answers from the existing
SessionFlow snapshot directly — no LLM round-trip for facts the backend
already has.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def route_global(text: str, sessions_repo) -> str:
    lowered = text.lower()
    if "ativa" in lowered or "ativas" in lowered:
        sessions = await sessions_repo.list_active()
        if not sessions:
            return "Nenhuma sessão ativa no momento."
        names = ", ".join(s["name"] for s in sessions)
        count = len(sessions)
        plural = "sessões" if count != 1 else "sessão"
        return f"Existem {count} {plural} ativas: {names}."
    return "Não entendi o comando. Diga o nome da sessão ou peça a lista de sessões ativas."


async def route_session(
    text: str, session_id: str, publish_command_fn, host_id: str | None = None
) -> None:
    """Fires the EXISTING `input` command (spec §11 — "não criar um segundo
    caminho de injeção no tmux"). The reply arrives asynchronously via the
    agent output bridge, not as this function's return value.

    Payload keys (`name`/`text`) match `command_consumer.py`'s
    `_handle_input` contract on the Worker side, not an arbitrary shape.

    `host_id` must be forwarded to `publish_command_fn` — `sessionflow` is a
    DIRECT exchange and each worker binds `sessionflow.commands.<host_id>`;
    without it the command routes to the unconsumed legacy key and is
    silently dropped (Critical #2, final review).
    """
    logger.warning(
        "voice.route_session publishing input session=%s host_id=%s chars=%d",
        session_id, host_id, len(text),
    )
    command_id = await publish_command_fn(
        type="input", payload={"name": session_id, "text": text}, host_id=host_id
    )
    logger.warning("voice.route_session published command_id=%s", command_id)
