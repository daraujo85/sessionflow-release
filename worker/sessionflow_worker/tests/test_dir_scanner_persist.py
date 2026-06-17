"""Integração da persistência/agendamento do scan de diretórios (TMUX-08).

Conecta no Mongo da stack via ``MONGO_URI_HOST`` e exercita ``persist_scan``
contra uma COLEÇÃO de teste isolada no DB ``sessionflow`` (o usuário de
aplicação só está autorizado nesse DB; criar um DB de teste daria erro de
autorização). A coleção tem nome único por execução e é dropada no teardown.

A raiz de scan é uma arvorezinha montada em ``tmp_path`` para não depender de
``~/dev``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

from sessionflow_worker.dir_scanner import persist_scan
from sessionflow_worker.mongo import get_db

# `.env` da raiz: worker/sessionflow_worker/tests/<this> -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)


def _host_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
async def coll_name():
    return f"host_directories_test_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name):
    uri = _host_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    database = get_db(uri=uri, db_name="sessionflow")
    client = database.client
    await database[coll_name].drop()
    try:
        yield database
    finally:
        await database[coll_name].drop()
        client.close()


@pytest.fixture
def tree(tmp_path: Path) -> list[Path]:
    """Monta uma arvorezinha de projetos sob ``tmp_path`` e a retorna como raiz."""
    (tmp_path / "proj_a" / "src").mkdir(parents=True)
    (tmp_path / "proj_b").mkdir()
    (tmp_path / ".hidden").mkdir()  # ignorado pela varredura
    return [tmp_path]


@pytest.mark.integration
async def test_persist_scan_upserts_docs(db, coll_name, tree) -> None:
    upserted = await persist_scan(db, roots=tree, collection=coll_name)
    assert upserted > 0

    count = await db[coll_name].count_documents({})
    assert count == upserted

    # Os diretórios visíveis foram persistidos; o oculto não.
    names = {doc["name"] async for doc in db[coll_name].find({})}
    assert {"proj_a", "proj_b", "src"} <= names
    assert ".hidden" not in names

    # Cada doc tem os campos esperados.
    sample = await db[coll_name].find_one({"name": "proj_a"})
    assert sample is not None
    assert set(sample) >= {"path", "parent", "name", "root", "scanned_at"}

    # O índice único em ``path`` existe.
    info = await db[coll_name].index_information()
    assert "uq_path" in info
    assert info["uq_path"].get("unique") is True


@pytest.mark.integration
async def test_persist_scan_idempotent(db, coll_name, tree) -> None:
    """Reexecução não duplica: mesma contagem, chave ``path``."""
    await persist_scan(db, roots=tree, collection=coll_name)
    count_first = await db[coll_name].count_documents({})

    await persist_scan(db, roots=tree, collection=coll_name)
    count_second = await db[coll_name].count_documents({})

    assert count_second == count_first

    # Não há paths duplicados.
    distinct_paths = await db[coll_name].distinct("path")
    assert len(distinct_paths) == count_second
