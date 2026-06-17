"""Cliente MongoDB do Worker (TMUX-01/03 — persistência de estado).

Obtém o database `motor` a partir de uma URI (env `MONGO_URI_HOST` com
prioridade sobre `MONGO_URI`) e garante os índices da coleção ``sessions``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, IndexModel

from sessionflow_worker.state import SessionState

# `.env` da raiz do repo: worker/sessionflow_worker/mongo.py -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

DEFAULT_DB_NAME = "sessionflow"
SESSIONS_COLLECTION = "sessions"

# Status considerados "ativos" — tudo exceto ``stopped``. O índice único
# parcial cobre só estes: o MongoDB não aceita ``$ne`` em
# ``partialFilterExpression``, então enumeramos os ativos via ``$in``.
ACTIVE_STATUSES = [s.value for s in SessionState if s is not SessionState.STOPPED]


def _load_env() -> None:
    """Carrega o `.env` da raiz sem sobrescrever variáveis já presentes."""
    if _ROOT_ENV.exists():
        load_dotenv(_ROOT_ENV, override=False)


def resolve_uri(uri: str | None = None) -> str:
    """Resolve a URI de conexão.

    Precedência: argumento explícito > ``MONGO_URI_HOST`` > ``MONGO_URI``.
    """
    if uri:
        return uri
    _load_env()
    host_uri = os.getenv("MONGO_URI_HOST")
    if host_uri:
        return host_uri
    plain_uri = os.getenv("MONGO_URI")
    if plain_uri:
        return plain_uri
    raise RuntimeError(
        "Nenhuma URI MongoDB configurada (defina MONGO_URI_HOST ou MONGO_URI)."
    )


def get_client(uri: str | None = None) -> AsyncIOMotorClient:
    """Retorna um client `motor` para a URI resolvida."""
    return AsyncIOMotorClient(resolve_uri(uri))


def get_db(
    uri: str | None = None,
    db_name: str | None = None,
) -> AsyncIOMotorDatabase:
    """Retorna o database `motor`.

    Permite injeção de ``uri`` e ``db_name`` para facilitar testes.
    """
    if not db_name:
        _load_env()
        db_name = os.getenv("MONGO_DB") or DEFAULT_DB_NAME
    return get_client(uri)[db_name]


async def ensure_indexes(
    db: AsyncIOMotorDatabase,
    collection: str = SESSIONS_COLLECTION,
) -> list[str]:
    """Cria os índices da coleção ``sessions``.

    Índices:
        - ``tmux_name`` único parcial (apenas sessões não-``stopped``).
        - ``status``.
        - ``updated_at``.

    ``collection`` permite injetar um nome alternativo (útil em testes que
    não podem criar um DB próprio). Retorna os nomes dos índices garantidos.
    """
    indexes = [
        IndexModel(
            [("tmux_name", ASCENDING)],
            name="uq_tmux_name_active",
            unique=True,
            partialFilterExpression={"status": {"$in": ACTIVE_STATUSES}},
        ),
        IndexModel([("status", ASCENDING)], name="ix_status"),
        IndexModel([("updated_at", ASCENDING)], name="ix_updated_at"),
    ]
    return await db[collection].create_indexes(indexes)
