"""Cliente RabbitMQ + topologia (TMUX-05/09/10/11).

Transporte do SessionFlow sobre RabbitMQ via ``aio-pika``.

Topologia (AD-010, isolamento por prefixo ``sessionflow``):
    - exchange direct ``sessionflow`` (durable)
    - fila ``sessionflow.commands`` (API -> Worker), bindada à routing key
      homônima ``sessionflow.commands``
    - routing key ``sessionflow.events`` (Worker -> API): os eventos são
      PUBLICADOS no exchange com essa routing key. A fila consumidora é a
      ``sessionflow.sse``, declarada/bindada pela própria API
      (``api/app/events_broker.py``); o worker NÃO declara nenhuma fila aqui.
    - vhost ``/``

Nota histórica: existia uma fila durável homônima ``sessionflow.events`` que
era declarada/bindada aqui mas NUNCA consumida (a API usa a sua própria
``sessionflow.sse``). Como não tinha consumidor, acumulava mensagens
indefinidamente. Ela foi removida da topologia — ``EVENTS_QUEUE`` continua
existindo apenas como ROUTING KEY de publicação dos eventos.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import aio_pika
from dotenv import load_dotenv

EXCHANGE_NAME = "sessionflow"
COMMANDS_QUEUE = "sessionflow.commands"
# ``EVENTS_QUEUE`` é, na prática, a ROUTING KEY usada pelos publishers de
# eventos (command_consumer._emit, output_capture._publish, discovery). NÃO há
# fila homônima declarada pelo worker: a fila consumidora é a ``sessionflow.sse``
# da API. O nome é mantido por compatibilidade de imports.
EVENTS_QUEUE = "sessionflow.events"
EVENTS_ROUTING_KEY = EVENTS_QUEUE

# .env fica na raiz do repo (dois níveis acima deste pacote).
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _resolve_uri(uri: str | None = None) -> str:
    """Resolve a URI de conexão.

    Precedência: argumento explícito > ``RABBITMQ_URI_HOST`` > ``RABBITMQ_URI``.
    Carrega o ``.env`` da raiz do repo (sem sobrescrever o ambiente já setado).
    """
    if uri:
        return uri

    load_dotenv(_ENV_PATH, override=False)
    resolved = os.environ.get("RABBITMQ_URI_HOST") or os.environ.get("RABBITMQ_URI")
    if not resolved:
        raise RuntimeError(
            "RabbitMQ URI não encontrada: defina RABBITMQ_URI_HOST ou RABBITMQ_URI "
            f"(verificado {_ENV_PATH})."
        )
    return resolved


async def connect(uri: str | None = None) -> aio_pika.abc.AbstractRobustConnection:
    """Abre uma conexão robusta (auto-reconexão) com o RabbitMQ."""
    return await aio_pika.connect_robust(_resolve_uri(uri))


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel,
) -> aio_pika.abc.AbstractExchange:
    """Declara exchange e a fila de comandos + bind. Idempotente.

    Declara apenas a fila ``sessionflow.commands`` (consumida pelo worker). A
    routing key ``sessionflow.events`` é só de PUBLICAÇÃO: a fila consumidora
    (``sessionflow.sse``) é declarada/bindada pela API. NÃO declaramos aqui a
    fila durável homônima ``sessionflow.events`` porque ninguém a consome e ela
    acumulava mensagens indefinidamente.

    Retorna o exchange ``sessionflow`` para reuso por publishers.
    """
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )

    queue = await channel.declare_queue(COMMANDS_QUEUE, durable=True)
    await queue.bind(exchange, routing_key=COMMANDS_QUEUE)

    return exchange


async def publish(
    channel: aio_pika.abc.AbstractChannel,
    routing_key: str,
    payload: dict,
) -> None:
    """Publica um payload JSON no exchange ``sessionflow`` com a routing key dada."""
    exchange = await channel.get_exchange(EXCHANGE_NAME)
    body = json.dumps(payload).encode("utf-8")
    message = aio_pika.Message(
        body=body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await exchange.publish(message, routing_key=routing_key)
