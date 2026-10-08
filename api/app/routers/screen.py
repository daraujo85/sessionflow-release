"""Read endpoint for the live screen mirror: ``GET /sessions/{id}/screen``.

The Worker upserts one document per session into ``session_screen`` with the
shape ``{tmux_name, text, at}`` by running ``tmux capture-pane -p`` (the
currently VISIBLE screen of the pane). Unlike ``/output`` (line-by-line,
append-only), this reflects what the TUI agent shows RIGHT NOW and is replaced
on every Worker cycle — ideal for mirroring agents that redraw the full screen.

Lives in its own router (separate from ``outputs.py`` to avoid route clashes)
but shares the ``/sessions`` prefix; FastAPI merges routers on the same prefix.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.repositories.sessions_repo import SessionsRepository
from app.timeutil import utc_aware

router = APIRouter(prefix="/sessions", tags=["screen"])


class ScreenOut(BaseModel):
    """Live screen snapshot returned by the API.

    ``text`` is the currently visible screen (pushed frequently via SSE).
    ``scrollback`` is the deeper terminal history (visible screen + rolled-back
    lines), populated only in the Mongo doc by the Worker and read ON-DEMAND
    here — never pushed over SSE. Empty string when the doc lacks it.
    """

    text: str = ""
    scrollback: str = ""
    at: datetime | None = None


@router.get("/{session_id}/screen", response_model=ScreenOut)
async def get_screen(request: Request, session_id: str) -> ScreenOut:
    # The Worker keys the screen doc by ``tmux_name``; the route receives the
    # Mongo ``_id``. Resolve _id -> tmux_name (with a fallback to the received
    # value, in case it already is a tmux_name) — same approach as /output.
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    session = await SessionsRepository(db, settings.sessions_collection).get_session(
        session_id
    )
    key = session.get("tmux_name", session_id) if session else session_id

    doc = await db[settings.screen_collection].find_one({"tmux_name": key})
    if not doc:
        return ScreenOut(text="", scrollback="", at=None)
    return ScreenOut(
        text=doc.get("text", "") or "",
        scrollback=doc.get("scrollback", "") or "",
        at=utc_aware(doc.get("at")),
    )
