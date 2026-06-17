"""Publisher of lifecycle commands to RabbitMQ (TMUX-05/06/07).

Publishes command messages to the ``sessionflow`` direct/durable exchange with
routing key ``sessionflow.commands``, matching the topology the Worker uses.

Message format (consumed by the Worker)::

    {
        "command_id": "<uuid>",
        "type": "<command type, e.g. create>",
        "payload": { ... },
        "requested_at": "<iso8601>"
    }
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import aio_pika

from app.config import Settings

EXCHANGE_NAME = "sessionflow"
COMMANDS_ROUTING_KEY = "sessionflow.commands"


async def publish_command(settings: Settings, type: str, payload: dict) -> str:
    """Publish a command to ``sessionflow.commands`` and return its ``command_id``.

    Opens a short-lived connection/channel, declares the ``sessionflow``
    exchange (direct, durable, idempotent), and publishes a persistent JSON
    message. The RabbitMQ URI is read from ``settings.effective_rabbitmq_uri``.
    """
    command_id = str(uuid.uuid4())
    message_body = {
        "command_id": command_id,
        "type": type,
        "payload": payload,
        "requested_at": datetime.now(UTC).isoformat(),
    }

    connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        message = aio_pika.Message(
            body=json.dumps(message_body).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(message, routing_key=COMMANDS_ROUTING_KEY)
    finally:
        await connection.close()

    return command_id
