"""Single-use, short-TTL tickets for authenticating `GET /ws/voice`.

WebSockets can't send an Authorization header from the browser, and a long
JWT in the URL leaks into logs/proxies (spec §20). Flow: the already-
authenticated Angular app calls `POST /api/voice/ticket`, gets a random
one-shot token, and opens `wss://.../ws/voice?ticket=...`. The gateway
consumes (and invalidates) it on connect.

Storage is an in-process dict on `app.state.voice_tickets` — tickets live
seconds, a single api replica is assumed for this MVP (same assumption as
`events_broker`'s in-process fan-out).
"""

from __future__ import annotations

import secrets
import time

TicketStore = dict[str, tuple[str, float]]  # ticket -> (user_id, expires_at)


def new_ticket(store: TicketStore, *, user_id: str, ttl_seconds: float = 45.0) -> str:
    now = time.monotonic()
    # Opportunistic sweep of abandoned tickets (Important #10, final
    # review) — a ticket issued but never consumed (tab closed before
    # connecting) would otherwise sit in `store` forever.
    expired = [t for t, (_uid, exp) in store.items() if exp <= now]
    for t in expired:
        del store[t]
    ticket = secrets.token_urlsafe(24)
    store[ticket] = (user_id, now + ttl_seconds)
    return ticket


def consume_ticket(store: TicketStore, ticket: str | None) -> str | None:
    """Returns the owning `user_id` and deletes the ticket, or `None` if
    missing/expired/already used."""
    if not ticket:
        return None
    entry = store.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    if time.monotonic() >= expires_at:
        return None
    return user_id
