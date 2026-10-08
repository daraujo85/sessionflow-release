"""Publisher de eventos transientes para o SSE (ex.: áudio do JARVIS).

Publica no exchange ``sessionflow`` com routing key ``sessionflow.events`` — a
mesma que o {@link EventsBroker} consome e fan-out'a para os clientes SSE. Ao
contrário do worker (que persiste eventos no Mongo via ``emit_event``), aqui
publicamos frames EFÊMEROS (não persistidos, ``NOT_PERSISTENT``): áudio em
base64 não deve inchar a coleção ``events``.
"""

from __future__ import annotations

import json

import aio_pika

from app.config import Settings

EXCHANGE_NAME = "sessionflow"
EVENTS_ROUTING_KEY = "sessionflow.events"


async def publish_event(settings: Settings, payload: dict) -> None:
    """Publica um frame transiente no canal de eventos (best-effort)."""
    connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
        )
        await exchange.publish(message, routing_key=EVENTS_ROUTING_KEY)
    finally:
        await connection.close()
