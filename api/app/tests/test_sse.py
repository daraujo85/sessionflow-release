"""Integration tests for the SSE EventsBroker + GET /events (DASH-01).

Runs on the host against the docker RabbitMQ, so it forces the host-facing
URI. Each test uses its OWN ephemeral, auto-deleted topic exchange and queue
(unique uuid-based names) so it never collides with the durable production
``sessionflow``/``sessionflow.sse`` topology that other tasks exercise in
parallel. Resources are torn down in fixtures.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from app.events_broker import EventsBroker
from app.routers.events import events as events_endpoint


def _rabbit_host_uri() -> str:
    return os.environ.get(
        "RABBITMQ_URI_HOST",
        "amqp://sessionflow:538aff09246ba916d6aeeaeac9f932a1@127.0.0.1:5672/",
    )


def _ephemeral_names() -> tuple[str, str, str]:
    token = uuid.uuid4().hex[:12]
    exchange = f"sessionflow_test_{token}"
    routing_key = "sessionflow.events"
    queue = f"sessionflow_sse_test_{token}"
    return exchange, routing_key, queue


@pytest.fixture
async def broker() -> EventsBroker:
    exchange, routing_key, queue = _ephemeral_names()
    b = EventsBroker(
        _rabbit_host_uri(),
        exchange_name=exchange,
        routing_key=routing_key,
        queue_name=queue,
        durable=False,
        auto_delete=True,
    )
    await b.start()
    assert b.started, "EventsBroker failed to connect to RabbitMQ (is the stack up?)"
    try:
        yield b
    finally:
        await b.stop()


@pytest.mark.integration
async def test_subscribe_receives_published_event(broker: EventsBroker) -> None:
    queue = await broker.subscribe()
    try:
        event = {"id": "evt-1", "session": "sess-a", "type": "status", "data": {"x": 1}}
        await broker.publish(event)
        received = await asyncio.wait_for(queue.get(), timeout=5.0)
        assert received["id"] == "evt-1"
        assert received["session"] == "sess-a"
        assert received["data"] == {"x": 1}
    finally:
        await broker.unsubscribe(queue)


@pytest.mark.integration
async def test_fan_out_to_multiple_subscribers(broker: EventsBroker) -> None:
    q1 = await broker.subscribe()
    q2 = await broker.subscribe()
    try:
        await broker.publish({"id": "evt-2", "session": "sess-b"})
        r1 = await asyncio.wait_for(q1.get(), timeout=5.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=5.0)
        assert r1["id"] == "evt-2"
        assert r2["id"] == "evt-2"
    finally:
        await broker.unsubscribe(q1)
        await broker.unsubscribe(q2)


@pytest.mark.integration
async def test_endpoint_streams_sse_frame() -> None:
    """Drive the GET /events handler and consume its SSE stream.

    We call the route handler directly with a fake ``Request`` (whose
    ``is_disconnected`` stays False) and read frames off the
    ``StreamingResponse.body_iterator``. This exercises the real handler,
    headers and frame formatting while letting us deterministically stop
    after the first data frame instead of fighting httpx stream teardown.
    """
    exchange, routing_key, queue_name = _ephemeral_names()

    isolated = EventsBroker(
        _rabbit_host_uri(),
        exchange_name=exchange,
        routing_key=routing_key,
        queue_name=queue_name,
        durable=False,
        auto_delete=True,
    )
    await isolated.start()
    assert isolated.started, "isolated broker failed to connect to RabbitMQ"

    class _FakeApp:
        class state:  # noqa: N801 - mimic Starlette app.state
            events_broker = isolated

    class _FakeRequest:
        app = _FakeApp

        async def is_disconnected(self) -> bool:
            return False

    try:
        response = await events_endpoint(
            _FakeRequest(),  # type: ignore[arg-type]
            session="sess-c",
            last_event_id=None,
        )

        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["connection"] == "keep-alive"

        frames: list[str] = []

        async def _consume() -> None:
            published = False
            async for chunk in response.body_iterator:
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                frames.append(text)
                if not published:
                    # First frame is the ": connected" comment; now publish.
                    await isolated.publish(
                        {"id": "evt-3", "session": "sess-c", "type": "log"}
                    )
                    published = True
                if any("data:" in f for f in frames):
                    break

        await asyncio.wait_for(_consume(), timeout=8.0)

        body = "".join(frames)
        assert ": connected" in body
        assert "id: evt-3" in body
        assert "data:" in body
        data_line = next(line for line in body.splitlines() if line.startswith("data:"))
        parsed = json.loads(data_line[len("data:"):].strip())
        assert parsed["session"] == "sess-c"
    finally:
        await isolated.stop()
