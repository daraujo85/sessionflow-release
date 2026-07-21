"""Helpers de fuso horário para datetimes vindos do Mongo.

Motor/pymongo devolvem datetimes NAIVE (sem tzinfo) mesmo quando o valor
gravado é UTC (``datetime.now(UTC)``). Sem tratamento, o Pydantic serializa
esses campos em JSON SEM sufixo de fuso (ex.: ``"2026-07-21T12:43:25.904"``
em vez de ``"...904Z"``). O ``new Date(...)`` do JavaScript interpreta uma
string ISO SEM fuso como HORÁRIO LOCAL do navegador, não UTC — deslocando o
valor em -3h (America/Sao_Paulo). Foi assim que ``next_run_at`` de um
comando a cada 15min aparecia como "próxima em 3h" no app (bug real
reportado: 15min→3h, 1h→4h, exatamente o offset de Brasília).

Anexar ``tzinfo=UTC`` antes de montar o modelo Pydantic resolve na raiz —
o mesmo padrão já existia isoladamente em ``routers/worker.py`` (``_aware``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utc_aware(dt: datetime | None) -> datetime | None:
    """Anexa ``tzinfo=UTC`` a um datetime naive; no-op se já tiver fuso."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def utc_aware_fields(data: dict[str, Any], *fields: str) -> dict[str, Any]:
    """Retorna uma cópia de ``data`` com ``utc_aware()`` aplicado aos ``fields``."""
    out = dict(data)
    for field in fields:
        if field in out:
            out[field] = utc_aware(out[field])
    return out
