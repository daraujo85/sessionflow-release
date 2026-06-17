"""Server-Sent Events endpoint: ``GET /events`` (DASH-01).

Streams live session events to the front-end over a single long-lived HTTP
connection. Events originate in RabbitMQ and are fanned out in-process by the
``EventsBroker`` stored on ``app.state.events_broker``. An optional ``session``
query parameter filters the stream to one session.

A heartbeat comment (``: ping``) is emitted roughly every 20 seconds so proxies
keep the connection open and the server detects client disconnects promptly.
``Last-Event-ID`` is accepted best-effort: it is echoed back as the SSE
``retry``/anchor but no event replay is performed (the source is a live queue).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.events_broker import EventsBroker

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 20.0

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _matches_session(payload: dict[str, Any], session: str | None) -> bool:
    if session is None:
        return True
    candidate = payload.get("session") or payload.get("session_id")
    return candidate == session


def _format_frame(payload: dict[str, Any]) -> str:
    event_id = payload.get("id") or payload.get("event_id") or payload.get("seq")
    data = json.dumps(payload, default=str)
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    event_type = payload.get("type") or payload.get("event")
    if event_type is not None:
        lines.append(f"event: {event_type}")
    lines.append(f"data: {data}")
    return "\n".join(lines) + "\n\n"


async def _event_stream(
    request: Request,
    broker: EventsBroker,
    session: str | None,
) -> AsyncIterator[str]:
    queue = await broker.subscribe()
    try:
        # Opening comment flushes headers and confirms the stream is live.
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if _matches_session(payload, session):
                yield _format_frame(payload)
    except asyncio.CancelledError:
        raise
    finally:
        await broker.unsubscribe(queue)


@router.get("/events")
async def events(
    request: Request,
    session: str | None = Query(default=None, description="Filter events to one session."),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    broker: EventsBroker = request.app.state.events_broker
    return StreamingResponse(
        _event_stream(request, broker, session),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
