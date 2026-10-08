"""Testes do runner/daemon do Worker (TMUX — orquestração).

Marker: ``integration`` (usa Mongo/Rabbit/tmux REAIS da stack local).

Princípios de segurança destes testes:
    - NÃO rodam o ``asyncio.gather`` infinito: ou exercitam peças isoladas
      (``reconcile_once`` uma vez, ``_capturable_sessions``), ou rodam o
      ``runner.run`` com ``stop`` setado / ``wait_for`` e timeout curto.
    - NÃO criam sessões tmux nem tocam sessões externas: a coleção de sessões
      é uma coleção de teste dedicada e dropada no teardown; o ``tmux ls`` do
      usuário fica inalterado.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from sessionflow_worker import rabbit, runner
from sessionflow_worker.discovery import (
    ORIGIN_EXTERNAL,
    ORIGIN_SESSIONFLOW,
    Discovery,
    ReconcileReport,
)
from sessionflow_worker.mongo import get_db
from sessionflow_worker.tmux_runtime import TmuxRuntime

# `.env` da raiz: worker/sessionflow_worker/tests/test_runner.py -> ../../../.env
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ROOT_ENV, override=False)

pytestmark = pytest.mark.integration


def _mongo_uri() -> str | None:
    return os.getenv("MONGO_URI_HOST") or os.getenv("MONGO_URI")


@pytest.fixture
def coll_name() -> str:
    return f"sessions_test_{uuid.uuid4().hex}"


@pytest.fixture
async def db(coll_name: str) -> AsyncIterator:
    uri = _mongo_uri()
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


async def test_capturable_sessions_includes_all_active(db, coll_name: str) -> None:
    """Captura pega TODAS as sessões ativas, inclusive externas (MVP).

    pipe-pane é read-only; monitorar as sessões reais do usuário é o objetivo.
    Inserimos sessionflow ativa, externa ativa e sessionflow parada: as duas
    ativas entram; a parada (≠ status ativo) nunca entra.
    """
    await db[coll_name].insert_many(
        [
            {
                "tmux_name": "sf-active",
                "origin": ORIGIN_SESSIONFLOW,
                "status": "running",
                "host_id": "test-host",
            },
            {
                "tmux_name": "ext-active",
                "origin": ORIGIN_EXTERNAL,
                "status": "running",
                "host_id": "test-host",
            },
            {
                "tmux_name": "sf-stopped",
                "origin": ORIGIN_SESSIONFLOW,
                "status": "stopped",
                "host_id": "test-host",
            },
        ]
    )

    names = await runner._capturable_sessions(db, "test-host", collection=coll_name)

    assert set(names) == {"sf-active", "ext-active"}  # ambas ativas capturadas
    assert "sf-stopped" not in names  # inativa nunca capturada


async def test_reconcile_once_via_runtime_does_not_explode(
    db, coll_name: str
) -> None:
    """Monta Discovery com infra real e roda UM reconcile sem criar sessões.

    Não criamos nenhuma sessão tmux: o reconcile apenas observa o que já existe
    no servidor (sessões reais do usuário) e faz upsert na coleção de TESTE.
    Como a coleção é dedicada/dropada, o estado real do usuário fica intacto.
    """
    runtime = TmuxRuntime()
    discovery = Discovery(runtime, db, "test-host", collection=coll_name)

    report = await discovery.reconcile_once()

    assert isinstance(report, ReconcileReport)
    # Nenhum doc com prefixo de teste sftest- foi criado por nós.
    leaked = await db[coll_name].count_documents(
        {"tmux_name": {"$regex": "^sftest-"}}
    )
    assert leaked == 0


async def test_run_stops_gracefully_with_event() -> None:
    """``runner.run`` encerra graciosamente quando o ``stop`` é setado.

    Setamos o evento de parada imediatamente e rodamos sob ``wait_for`` com
    timeout curto: o daemon deve montar a infra, ver o stop e retornar SEM
    exceção não-tratada e SEM rodar para sempre.
    """
    uri = _mongo_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    # Confirma que o Rabbit está acessível; senão, é skip (não falha).
    try:
        conn = await rabbit.connect()
        await conn.close()
    except Exception:  # noqa: BLE001
        pytest.skip("RabbitMQ indisponível para o teste de boot do runner.")

    stop = asyncio.Event()
    stop.set()  # pede shutdown imediato no próximo ponto de checagem.

    # Não deve estourar timeout (gather infinito) nem propagar exceção.
    await asyncio.wait_for(runner.run(stop=stop), timeout=10)


async def test_run_times_out_without_unhandled_error() -> None:
    """Roda o daemon de verdade por ~2s e cancela: sem exceção não-tratada.

    Aqui o ``stop`` NÃO é setado: deixamos o gather rodar de fato (discovery,
    consumer, dir_scanner, capture_loop) e cancelamos via ``wait_for`` timeout.
    O ``CancelledError`` resultante é esperado e tratado.
    """
    uri = _mongo_uri()
    if not uri:
        pytest.skip("MONGO_URI_HOST/MONGO_URI não configurada.")
    try:
        conn = await rabbit.connect()
        await conn.close()
    except Exception:  # noqa: BLE001
        pytest.skip("RabbitMQ indisponível para o teste de boot do runner.")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(runner.run(), timeout=2)
