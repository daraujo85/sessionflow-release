"""Emissão de eventos do SessionFlow (DASH-03).

Um *event* descreve algo que aconteceu com uma sessão (criada, parada,
detached, etc.). Cada evento é:

- **persistido** na coleção ``events`` do Mongo (fonte de verdade, com ``seq``
  incremental por coleção para ordenação estável e ``at`` em UTC);
- **publicado** (opcionalmente) no RabbitMQ para consumo em tempo real pela API
  / dashboard, reusando ``rabbit.publish`` sobre o exchange ``sessionflow``.

A publicação só ocorre quando um ``channel`` aio-pika é fornecido; sem channel,
o evento vive apenas no Mongo (modo degradado / testes sem broker).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aio_pika
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker import rabbit

EVENTS_COLLECTION = "events"

# Tipos de evento (o que aconteceu).
EVENT_TYPES = frozenset(
    {
        "created",
        "stopped",
        "detached",
        "completed",
        "error",
        "output",
        "input",
        # Pede atenção do usuário: aguardando resposta/decisão, ou bloco
        # concluído/ocioso. Frontend trata como notificação (sino + sistema).
        "attention",
    }
)

# Severidade / natureza do evento (como apresentar no dashboard).
EVENT_KINDS = frozenset({"attention", "info", "warning", "success"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _next_seq(coll) -> int:
    """Retorna o próximo ``seq`` (incremental por coleção, começando em 1)."""
    last = await coll.find_one(sort=[("seq", -1)], projection={"seq": 1})
    if last and isinstance(last.get("seq"), int):
        return last["seq"] + 1
    return 1


async def emit_event(
    db: AsyncIOMotorDatabase,
    type: str,
    kind: str,
    session_id: Any,
    title: str,
    desc: str,
    channel: aio_pika.abc.AbstractChannel | None = None,
    exchange_name: str = rabbit.EXCHANGE_NAME,
    routing_key: str = rabbit.EVENTS_QUEUE,
    collection: str = EVENTS_COLLECTION,
) -> dict[str, Any]:
    """Persiste um evento no Mongo e (se houver ``channel``) o publica no Rabbit.

    Args:
        db: database ``motor``.
        type: tipo do evento; deve pertencer a :data:`EVENT_TYPES`.
        kind: severidade; deve pertencer a :data:`EVENT_KINDS`.
        session_id: identificador da sessão associada.
        title: título curto do evento.
        desc: descrição detalhada.
        channel: canal aio-pika; se ``None``, não publica (só Mongo).
        exchange_name: exchange para publicação (default ``sessionflow``).
        routing_key: routing key (default ``sessionflow.events``).
        collection: coleção Mongo (default ``events``); injetável p/ testes.

    Returns:
        O documento persistido (JSON-serializável), incluindo ``seq`` e ``at``.

    Raises:
        ValueError: se ``type`` ou ``kind`` forem inválidos.
    """
    if type not in EVENT_TYPES:
        raise ValueError(f"type inválido: {type!r} (esperado um de {sorted(EVENT_TYPES)})")
    if kind not in EVENT_KINDS:
        raise ValueError(f"kind inválido: {kind!r} (esperado um de {sorted(EVENT_KINDS)})")

    coll = db[collection]
    seq = await _next_seq(coll)
    at = _now()

    doc: dict[str, Any] = {
        "session_id": session_id,
        "type": type,
        "kind": kind,
        "title": title,
        "desc": desc,
        "at": at,
        "seq": seq,
    }

    await coll.insert_one(doc)

    if channel is not None:
        # Payload JSON-serializável: datetime -> ISO 8601; sem o _id do Mongo.
        payload = {k: v for k, v in doc.items() if k != "_id"}
        payload["at"] = at.isoformat()
        payload["session_id"] = (
            session_id
            if session_id is None or isinstance(session_id, (str, int, float, bool))
            else str(session_id)
        )
        # Publica direto no exchange nomeado (honra ``exchange_name``, que pode
        # ser uma exchange efêmera em testes). ``rabbit.publish`` é fixo no
        # exchange durável ``sessionflow``, então replicamos a mensagem aqui.
        exchange = await channel.get_exchange(exchange_name)
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(message, routing_key=routing_key)

    # O insert_one anexa o _id ao doc; remove p/ devolver algo serializável.
    doc.pop("_id", None)
    return doc
