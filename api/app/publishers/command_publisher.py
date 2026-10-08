"""Publisher of lifecycle commands to RabbitMQ (TMUX-05/06/07, AD-011).

Publishes command messages to the ``sessionflow`` direct/durable exchange.
**Multi-host:** cada worker (host) consome só a SUA fila
(``sessionflow.commands.<host_id>``) — o publisher precisa saber o
``host_id`` de DESTINO (o da sessão alvo) pra rotear certo; sem isso, dois
hosts consumindo a mesma fila fariam o RabbitMQ distribuir comandos
round-robin entre eles (comando de uma sessão do host A podia cair no
worker do host B, que não tem aquele tmux). Ver ``docs/multi-host-plan.md``.

Message format (consumed by the Worker)::

    {
        "command_id": "<uuid>",
        "type": "<command type, e.g. create>",
        "payload": { ... },
        "requested_at": "<iso8601>"
    }
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import aio_pika

from app.config import Settings

EXCHANGE_NAME = "sessionflow"
# Fallback histórico (pré multi-host) — só usado se ``host_id`` não puder ser
# resolvido (ex.: nenhum worker jamais fez heartbeat). Não há mais worker
# consumindo essa routing key sem sufixo em condições normais.
COMMANDS_ROUTING_KEY = "sessionflow.commands"

_connection: aio_pika.abc.AbstractRobustConnection | None = None
_connection_lock = asyncio.Lock()


def _routing_key_for(host_id: str | None) -> str:
    return f"{COMMANDS_ROUTING_KEY}.{host_id}" if host_id else COMMANDS_ROUTING_KEY


async def _get_connection(settings: Settings) -> aio_pika.abc.AbstractRobustConnection:
    """Conexão AMQP cacheada no processo — evita handshake TCP+AMQP a cada
    comando (era o gargalo de latência no envio de texto pro terminal).
    Reconecta se a conexão cacheada morreu."""
    global _connection
    async with _connection_lock:
        if _connection is None or _connection.is_closed:
            _connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
        return _connection


async def publish_command(
    settings: Settings, type: str, payload: dict, host_id: str | None = None
) -> str:
    """Publish a command and return its ``command_id``.

    ``host_id`` (do doc da sessão alvo, ou resolvido p/ ``create``) decide a
    routing key — cada worker só consome a fila do seu próprio host. Reuses a
    process-wide cached AMQP connection (see ``_get_connection``), opens a
    fresh channel per call (closed at the end of the call, so channels never
    accumulate on the cached connection — RabbitMQ enforces a ``channel_max``
    per connection), declares the ``sessionflow`` exchange (direct, durable,
    idempotent), and publishes a persistent JSON message. The RabbitMQ URI is
    read from ``settings.effective_rabbitmq_uri``.
    """
    command_id = str(uuid.uuid4())
    message_body = {
        "command_id": command_id,
        "type": type,
        "payload": payload,
        "requested_at": datetime.now(UTC).isoformat(),
    }

    connection = await _get_connection(settings)
    channel = await connection.channel()
    async with channel:
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
        await exchange.publish(message, routing_key=_routing_key_for(host_id))

    return command_id
