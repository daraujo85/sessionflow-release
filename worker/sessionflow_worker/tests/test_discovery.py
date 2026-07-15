"""Testes de integração da reconciliação Discovery (tmux + Mongo REAIS).

Marker: ``integration``. Isolamento:
    - Coleção Mongo dedicada ``sessions_test_<uuid>`` no DB ``sessionflow``
      (o usuário de app só tem permissão nesse DB), dropada no teardown.
    - Sessões tmux namespaced ``sftest-<uuid>``; o teardown mata APENAS
      sessões com prefixo ``sftest-`` — sessões reais do usuário jamais são
      tocadas.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from sessionflow_worker.discovery import ORIGIN_EXTERNAL, Discovery, ReconcileReport
from sessionflow_worker.mongo import get_db
from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxRuntimeError

# `.env` da raiz: worker/sessionflow_worker/tests/test_discovery.py -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)

pytestmark = pytest.mark.integration

_PREFIX = "sftest-"


def _host_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
def coll_name() -> str:
    return f"sessions_test_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name: str) -> AsyncIterator:
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
def runtime() -> TmuxRuntime:
    return TmuxRuntime()


@pytest.fixture
def make_name(runtime: TmuxRuntime) -> Iterator[Callable[[], str]]:
    """Gera nomes ``sftest-*`` e mata TODOS no teardown (apenas ``sftest-*``)."""
    created: list[str] = []

    def _make() -> str:
        name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
        created.append(name)
        return name

    try:
        yield _make
    finally:
        for name in created:
            assert name.startswith(_PREFIX)  # cinto de segurança
            try:
                if runtime.has_session(name):
                    runtime.kill_session(name)
            except TmuxRuntimeError:
                pass


@pytest.fixture
def discovery(runtime: TmuxRuntime, db, coll_name: str) -> Discovery:
    return Discovery(runtime, db, "test-host", collection=coll_name)


async def test_external_session_discovered_as_external(
    discovery: Discovery,
    runtime: TmuxRuntime,
    make_name: Callable[[], str],
    db,
    coll_name: str,
    tmp_path,
) -> None:
    """(a) Sessão criada fora do SessionFlow aparece com origin=externa."""
    name = make_name()
    runtime.new_session(name, tmp_path)

    report = await discovery.reconcile_once()
    assert isinstance(report, ReconcileReport)
    assert report.discovered >= 1

    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc is not None
    assert doc["origin"] == ORIGIN_EXTERNAL
    assert doc["display_name"] == name
    assert doc["tmux_session_id"].startswith("$")
    assert doc["agent_pid"] is not None and doc["agent_pid"] > 0
    # Sessão detached e viva -> derive_state => "detached".
    assert doc["status"] == "detached"
    assert "created_at" in doc and "updated_at" in doc and "last_seen_at" in doc


async def test_work_dir_persisted_from_pane(
    discovery: Discovery,
    runtime: TmuxRuntime,
    make_name: Callable[[], str],
    db,
    coll_name: str,
) -> None:
    """O upsert grava o ``work_dir`` a partir do pane da sessão (``/tmp``)."""
    name = make_name()
    runtime.new_session(name, "/tmp")

    await discovery.reconcile_once()

    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc is not None
    assert "work_dir" in doc
    assert doc["work_dir"].endswith("/tmp") or doc["work_dir"] == "/tmp"


async def test_vanished_session_becomes_stopped(
    discovery: Discovery,
    runtime: TmuxRuntime,
    make_name: Callable[[], str],
    db,
    coll_name: str,
    tmp_path,
) -> None:
    """(b) Sessão que some do tmux vira ``stopped`` no próximo reconcile."""
    name = make_name()
    runtime.new_session(name, tmp_path)

    await discovery.reconcile_once()
    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc is not None
    assert doc["status"] != "stopped"

    # Mata a sessão e reconcilia de novo.
    runtime.kill_session(name)
    report = await discovery.reconcile_once()
    assert report.stopped >= 1

    doc = await db[coll_name].find_one({"tmux_name": name})
    assert doc is not None
    assert doc["status"] == "stopped"
    assert doc["agent_pid"] is None


async def test_reconcile_without_sftest_sessions_does_not_break(
    discovery: Discovery,
    db,
    coll_name: str,
) -> None:
    """(c) Sem sessões sftest, reconcile roda sem quebrar e não cria lixo."""
    report = await discovery.reconcile_once()
    assert isinstance(report, ReconcileReport)

    # Nenhum doc sftest-* deve ter sido criado por este ciclo.
    count = await db[coll_name].count_documents(
        {"tmux_name": {"$regex": f"^{_PREFIX}"}}
    )
    assert count == 0


async def test_concurrent_reconcile_serialized_by_lock(
    discovery: Discovery,
    runtime: TmuxRuntime,
    make_name: Callable[[], str],
    db,
    coll_name: str,
    tmp_path,
) -> None:
    """(d) Duas reconciliações concorrentes são serializadas pelo lock.

    O lock garante que não há corrupção: a sessão é descoberta exatamente uma
    vez (um único doc) somando-se os ``discovered`` dos dois ciclos.
    """
    name = make_name()
    runtime.new_session(name, tmp_path)

    # As duas chamadas concorrem; o lock as serializa. Sem o lock, o segundo
    # ciclo poderia ler antes do upsert do primeiro e inserir um duplicado
    # (violando ou disputando a unicidade), ou corromper as contagens.
    r1, r2 = await asyncio.gather(
        discovery.reconcile_once(),
        discovery.reconcile_once(),
    )

    # Exatamente um doc para a nossa sessão, sem duplicatas (upsert idempotente
    # sob o lock). Esta é a garantia central de não-corrupção.
    count = await db[coll_name].count_documents({"tmux_name": name})
    assert count == 1

    # Ambos os ciclos completaram sem erro e retornaram relatórios.
    assert isinstance(r1, ReconcileReport)
    assert isinstance(r2, ReconcileReport)
    # O segundo ciclo nunca "redescobre" o que o primeiro já persistiu:
    # como o lock serializa, o segundo vê o doc e não o conta como discovered.
    assert r2.discovered == 0
