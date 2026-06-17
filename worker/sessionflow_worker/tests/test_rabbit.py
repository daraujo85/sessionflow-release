"""Testes de transporte RabbitMQ (TMUX-05/09/10/11).

Integração: usa o RabbitMQ real da stack (RABBITMQ_URI_HOST).
"""

import asyncio
import json
import uuid

import pytest

from sessionflow_worker import rabbit


@pytest.mark.integration
async def test_declare_topology_is_idempotent() -> None:
    conn = await rabbit.connect()
    try:
        channel = await conn.channel()
        # Duas chamadas seguidas não devem falhar (idempotência).
        exchange1 = await rabbit.declare_topology(channel)
        exchange2 = await rabbit.declare_topology(channel)
        assert exchange1.name == rabbit.EXCHANGE_NAME
        assert exchange2.name == rabbit.EXCHANGE_NAME
    finally:
        await conn.close()


@pytest.mark.integration
async def test_publish_and_consume_roundtrip() -> None:
    conn = await rabbit.connect()
    try:
        channel = await conn.channel()
        await rabbit.declare_topology(channel)

        # Fila/rota de teste efêmera p/ não vazar nas filas reais.
        routing_key = f"sessionflow.test.{uuid.uuid4().hex}"
        exchange = await channel.get_exchange(rabbit.EXCHANGE_NAME)
        queue = await channel.declare_queue(routing_key, durable=False, auto_delete=True)
        await queue.bind(exchange, routing_key=routing_key)

        try:
            payload = {"kind": "ping", "nonce": uuid.uuid4().hex}
            await rabbit.publish(channel, routing_key, payload)

            incoming = await asyncio.wait_for(queue.get(timeout=5), timeout=5)
            assert incoming is not None
            assert incoming.content_type == "application/json"
            assert json.loads(incoming.body) == payload
            await incoming.ack()
        finally:
            await queue.purge()
            await queue.unbind(exchange, routing_key=routing_key)
            await queue.delete(if_unused=False, if_empty=False)
    finally:
        await conn.close()


@pytest.mark.integration
async def test_publish_to_commands_queue() -> None:
    """Publica de fato na fila real sessionflow.commands e consome de volta."""
    conn = await rabbit.connect()
    try:
        channel = await conn.channel()
        await rabbit.declare_topology(channel)

        nonce = uuid.uuid4().hex
        payload = {"cmd": "noop", "nonce": nonce}
        await rabbit.publish(channel, rabbit.COMMANDS_QUEUE, payload)

        queue = await channel.get_queue(rabbit.COMMANDS_QUEUE)
        incoming = await asyncio.wait_for(queue.get(timeout=5), timeout=5)
        assert incoming is not None
        assert json.loads(incoming.body)["nonce"] == nonce
        await incoming.ack()
    finally:
        await conn.close()
