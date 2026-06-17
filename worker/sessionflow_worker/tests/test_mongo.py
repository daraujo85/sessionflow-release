"""Integração do cliente MongoDB do Worker (TMUX-01/03).

Conecta no Mongo da stack via ``MONGO_URI_HOST``, roda ``ensure_indexes`` numa
coleção de teste isolada, faz insert/find e verifica que os índices existem.

Nota: o usuário de aplicação ``sessionflow`` só está autorizado no DB
``sessionflow`` (authSource=sessionflow); criar um DB ``sessionflow_test`` daria
erro de autorização. Por isso isolamos via uma COLEÇÃO de teste dedicada
(com nome único por execução) e a dropamos no teardown.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pymongo.errors import DuplicateKeyError

from sessionflow_worker.mongo import ACTIVE_STATUSES, ensure_indexes, get_db

# `.env` da raiz: worker/sessionflow_worker/tests/test_mongo.py -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)


def _host_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
async def coll_name():
    return f"sessions_test_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name):
    uri = _host_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    # DB acessível pelo usuário de aplicação; coleção de teste isolada.
    database = get_db(uri=uri, db_name="sessionflow")
    client = database.client
    await database[coll_name].drop()
    try:
        yield database
    finally:
        await database[coll_name].drop()
        client.close()


@pytest.mark.integration
async def test_ensure_indexes_and_crud(db, coll_name) -> None:
    created = await ensure_indexes(db, collection=coll_name)
    assert "uq_tmux_name_active" in created
    assert "ix_status" in created
    assert "ix_updated_at" in created

    # Os índices realmente existem na coleção.
    info = await db[coll_name].index_information()
    assert "uq_tmux_name_active" in info
    assert "ix_status" in info
    assert "ix_updated_at" in info

    # O índice único parcial tem o partialFilterExpression esperado.
    assert info["uq_tmux_name_active"].get("unique") is True
    pfe = info["uq_tmux_name_active"]["partialFilterExpression"]
    assert set(pfe["status"]["$in"]) == set(ACTIVE_STATUSES)
    assert "stopped" not in pfe["status"]["$in"]

    # insert / find round-trip.
    doc = {
        "tmux_name": "sess-1",
        "status": "running",
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db[coll_name].insert_one(doc)
    assert result.inserted_id is not None

    found = await db[coll_name].find_one({"tmux_name": "sess-1"})
    assert found is not None
    assert found["status"] == "running"


@pytest.mark.integration
async def test_unique_index_allows_multiple_stopped(db, coll_name) -> None:
    """Sessões ``stopped`` não disputam a unicidade de ``tmux_name``."""
    await ensure_indexes(db, collection=coll_name)

    now = datetime.now(timezone.utc)
    await db[coll_name].insert_one(
        {"tmux_name": "dup", "status": "stopped", "updated_at": now}
    )
    # Segundo doc stopped com mesmo tmux_name é permitido (fora do filtro parcial).
    await db[coll_name].insert_one(
        {"tmux_name": "dup", "status": "stopped", "updated_at": now}
    )

    count = await db[coll_name].count_documents({"tmux_name": "dup"})
    assert count == 2


@pytest.mark.integration
async def test_unique_index_blocks_duplicate_active(db, coll_name) -> None:
    """Duas sessões ativas com o mesmo ``tmux_name`` violam a unicidade."""
    await ensure_indexes(db, collection=coll_name)

    now = datetime.now(timezone.utc)
    await db[coll_name].insert_one(
        {"tmux_name": "live", "status": "running", "updated_at": now}
    )
    with pytest.raises(DuplicateKeyError):
        await db[coll_name].insert_one(
            {"tmux_name": "live", "status": "waiting_input", "updated_at": now}
        )
