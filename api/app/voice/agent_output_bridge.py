"""Turns the EXISTING per-line output stream (`output_capture.py` →
`session_output` → `sessionflow.events` → `EventsBroker`) into the
`session.response.delta`/`.end` shape the spec describes (§11) — no new
plumbing between the Worker and the API, just a consumer shaped for voice.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

# `line_type`s que nunca são resposta falável: eco de comando digitado
# (`cmd`) e trace de chamada de ferramenta (`tool`, ex.: linhas ``⎿ ...``).
# `sys`/`out`/`ask`/`agent` continuam elegíveis — prosa real do agente sem
# prefixo especial cai em `out` (default de `classify_line`).
_UNSPEAKABLE_LINE_TYPES = {"cmd", "tool"}

# Linha sem nenhuma letra (spinner "✢ Cogitando...", moldura "╭─╮│╰─╯") é só
# símbolo/UI chrome do terminal — nunca é resposta, mesmo com `line_type`
# "falável" (cai em `out`/`sys` por não bater nenhum prefixo de classify_line).
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Statuses meaning "the agent has stopped producing output for this turn" —
# same vocabulary the terminal/session UI already uses for waiting states.
_DONE_STATUSES = {"waiting_input", "waiting_confirmation", "idle"}

# `status` alone never reaches "idle" for a normal reply without an explicit
# question (discovery.py keeps it "running" on purpose, so a session doesn't
# vanish from the Home while the agent waits for the user to think — see
# discovery.py's `_screen_idle` comment). `attention` is the finer-grained
# label discovery.py derives from the same idle-screen check; it DOES flip to
# "idle"/"waiting" in that case, so the bridge also treats it as turn-done.
_DONE_ATTENTION = {"waiting", "idle"}


def _normalize_for_echo(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lstrip("> $#❯›")).strip().lower()


def _is_echo(line: str, echo_text: str) -> bool:
    """`line` is (part of) the CLI echoing `echo_text` back on screen.

    A long question wraps into several pane lines, so exact match alone
    misses it — the terminal reflows the same string across N physical
    lines. Any normalized line that's a substring of the normalized
    `echo_text` (and not just noise) counts as an echo fragment.
    """
    norm_line = _normalize_for_echo(line)
    norm_echo = _normalize_for_echo(echo_text)
    return bool(norm_line) and norm_line in norm_echo


async def bridge_agent_output(
    session_id: str,
    broker,
    on_text_delta: Callable[[str], Awaitable[None]],
    on_done: Callable[[], Awaitable[None]],
    echo_text: str | None = None,
) -> None:
    """Consume `broker`'s fan-out queue, filtered to `session_id`.

    `echo_text`, when given, is the text this SAME turn just typed into the
    session (`route_session`'s `text` arg) — the CLI echoes it back on
    screen as ordinary "new" output, indistinguishable by `line_type` from
    the agent's own reply. Any line that normalizes to the same text (prompt
    decoration like `>`/`$` stripped) is skipped instead of spoken back.

    Calls `on_text_delta` as soon as EACH matching line arrives (never
    buffers the full reply first — that's what lets TTS start on the first
    sentence while the agent is still writing the rest). Returns once a
    `session_status`/`status` event reports the session back in a
    `_DONE_STATUSES` state, or `attention` in `_DONE_ATTENTION` (the only
    signal for a normal reply with no explicit question — `status` stays
    "running" for those on purpose), after calling `on_done`. `discovery.py`
    already emits `type="status"` with `status`/`attention` flattened to the
    event's top level by `emit_event`'s `extra` handling — no extra plumbing
    needed, only this bridge recognizing the fields it actually publishes.

    Skips `line_type` in `_UNSPEAKABLE_LINE_TYPES` (command echo, tool trace)
    and any line with no letters (spinner/box-drawing chrome) — TUI noise
    that `classify_line` can't tell apart from real prose by prefix alone.

    Always unsubscribes from `broker` on the way out (normal return or the
    task being cancelled) — otherwise every session-mode turn leaks a
    subscriber queue forever (Critical #3, final review).
    """
    queue = await broker.subscribe()
    try:
        while True:
            event = await queue.get()
            candidate_session = event.get("session") or event.get("session_id")
            if candidate_session != session_id:
                continue
            if event.get("type") in ("session_status", "status") and (
                event.get("status") in _DONE_STATUSES
                or event.get("attention") in _DONE_ATTENTION
            ):
                await on_done()
                return
            # Only real per-line agent output carries `line_type` (see
            # `output_capture.py::_publish`) — screen-mirror pushes
            # (`kind: "screen"`) and `jarvis_audio` frames also carry a
            # `text`/`session_id` pair and would otherwise be misread as
            # agent replies and spoken back (Important #12).
            if "line_type" not in event:
                continue
            # `snapshot=True` marks pane content that existed BEFORE this
            # turn (replayed by `start_capture` after a worker restart) —
            # never speak it, only genuinely new output (spec: falar só o
            # que foi gerado depois da mensagem do usuário).
            if event.get("snapshot"):
                continue
            if event.get("line_type") in _UNSPEAKABLE_LINE_TYPES:
                continue
            text = event.get("text")
            if not text or not _HAS_LETTER_RE.search(text):
                continue
            if echo_text is not None and _is_echo(text, echo_text):
                continue
            await on_text_delta(text)
    finally:
        await broker.unsubscribe(queue)
