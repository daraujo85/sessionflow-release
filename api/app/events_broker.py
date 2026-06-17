"""In-process fan-out broker for SSE events sourced from RabbitMQ (DASH-01).

Mongo is a standalone (no change streams), so live updates reach the API via
RabbitMQ. ``EventsBroker`` holds a single ``aio-pika`` connection, consumes a
queue bound to the ``sessionflow`` topic exchange (routing key
``sessionflow.events``) and fans every message out, in-process, to a set of
``asyncio.Queue`` subscribers. Each ``GET /events`` request subscribes, reads
from its own queue, and unsubscribes on disconnect.

The exchange and queue names are configurable so tests can run against an
ephemeral, auto-deleted exchange/queue instead of the durable production one
(``sessionflow.sse``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aio_pika

logger = logging.getLogger(__name__)

DEFAULT_EXCHANGE = "sessionflow"
DEFAULT_ROUTING_KEY = "sessionflow.events"
DEFAULT_QUEUE = "sessionflow.sse"


class EventsBroker:
    """Consumes RabbitMQ events and fans them out to in-process subscribers."""

    def __init__(
        self,
        rabbitmq_uri: str,
        *,
        exchange_name: str = DEFAULT_EXCHANGE,
        routing_key: str = DEFAULT_ROUTING_KEY,
        queue_name: str = DEFAULT_QUEUE,
        durable: bool = True,
        auto_delete: bool = False,
        queue_max_size: int = 1000,
    ) -> None:
        self._uri = rabbitmq_uri
        self._exchange_name = exchange_name
        self._routing_key = routing_key
        self._queue_name = queue_name
        self._durable = durable
        self._auto_delete = auto_delete
        self._queue_max_size = queue_max_size

        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._queue: aio_pika.abc.AbstractQueue | None = None
        self._consumer_tag: str | None = None

        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        """Open the connection, declare the exchange/queue and start consuming.

        Never raises: a Rabbit outage must not crash the app. On failure the
        broker stays in a not-started state and ``subscribe`` still works (it
        simply receives nothing until a future restart).
        """
        if self._started:
            return
        try:
            self._connection = await aio_pika.connect_robust(self._uri)
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=100)

            # DIRECT para casar com o worker (rabbit.py declara `sessionflow`
            # como DIRECT). Declarar com tipo divergente dá PRECONDITION_FAILED
            # e o broker nunca consome → SSE morto.
            exchange = await self._channel.declare_exchange(
                self._exchange_name,
                aio_pika.ExchangeType.DIRECT,
                durable=self._durable,
            )
            self._queue = await self._channel.declare_queue(
                self._queue_name,
                durable=self._durable,
                auto_delete=self._auto_delete,
            )
            await self._queue.bind(exchange, routing_key=self._routing_key)
            self._consumer_tag = await self._queue.consume(self._on_message)
            self._started = True
            logger.info(
                "EventsBroker started (exchange=%s queue=%s)",
                self._exchange_name,
                self._queue_name,
            )
        except Exception:
            logger.warning("EventsBroker failed to start; continuing without Rabbit", exc_info=True)
            await self._safe_close()

    async def stop(self) -> None:
        """Stop consuming and close the connection. Never raises."""
        self._started = False
        await self._safe_close()
        async with self._lock:
            self._subscribers.clear()

    async def _safe_close(self) -> None:
        if self._queue is not None and self._consumer_tag is not None:
            try:
                await self._queue.cancel(self._consumer_tag)
            except Exception:
                logger.debug("Error cancelling consumer", exc_info=True)
        self._consumer_tag = None
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                logger.debug("Error closing Rabbit connection", exc_info=True)
        self._connection = None
        self._channel = None
        self._queue = None

    async def _on_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(ignore_processed=True):
            payload = self._decode(message.body)
            await self._fan_out(payload)

    @staticmethod
    def _decode(body: bytes) -> dict[str, Any]:
        try:
            data = json.loads(body.decode("utf-8"))
            if isinstance(data, dict):
                return data
            return {"data": data}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"data": body.decode("utf-8", errors="replace")}

    async def _fan_out(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest to keep the stream live.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.debug("Dropping event for full subscriber queue")

    async def publish(self, payload: dict[str, Any]) -> None:
        """Publish an event to the exchange (used by tests / internal producers)."""
        if self._channel is None:
            raise RuntimeError("EventsBroker not started; cannot publish")
        exchange = await self._channel.get_exchange(self._exchange_name)
        body = json.dumps(payload).encode("utf-8")
        await exchange.publish(
            aio_pika.Message(body=body, content_type="application/json"),
            routing_key=self._routing_key,
        )

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register and return a new subscriber queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_max_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(queue)
