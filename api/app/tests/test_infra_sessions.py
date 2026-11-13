"""Guarda de sessões de INFRAESTRUTURA na API (túnel ``cf``, worker).

Incidente real (nov/2026): a sessão ``cf`` (túnel do proxy Mongo+RabbitMQ
nos hosts Colab/Kaggle) aparecia na lista de Sessões como se fosse trabalho.
A limpeza de 07/11 pediu o "delete" dela; com o host offline o comando ficou
enfileirado no RabbitMQ e, quando o worker voltou, matou o próprio túnel — o
heartbeat deixou de chegar e o host ficou travado em "Ligando" pra sempre.

Dois comportamentos precisam ficar travados aqui:
1. a listagem de Sessões NUNCA mostra ``cf`` (nem as outras de infra);
2. pedir kill/delete de uma sessão de infra dá 409, em vez de publicar um
   comando que derrubaria o host.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.infra_sessions import (
    INFRA_SESSION_NAMES,
    INFRA_SESSION_PREFIX,
    is_infra_session,
)
from app.repositories.sessions_repo import SessionsRepository


def test_cf_is_infra() -> None:
    assert is_infra_session("cf")
    assert "cf" in INFRA_SESSION_NAMES
    assert is_infra_session("sessionflow-worker")
    assert is_infra_session("cloudflared-tunnel")
    assert not is_infra_session("meu-projeto")


def test_reserved_prefix_is_infra() -> None:
    """O prefixo reservado cobre toda infra nova (``sfinfra-*``)."""
    assert is_infra_session(f"{INFRA_SESSION_PREFIX}tunnel")
    assert is_infra_session(f"{INFRA_SESSION_PREFIX}worker")
    assert not is_infra_session("sfusage-abc")  # scraper, não infra


def test_api_and_worker_lists_are_in_sync() -> None:
    """As duas listas de infra (API e worker) NÃO podem divergir.

    API e worker são processos separados (a API não importa o pacote do
    worker), então a lista é duplicada — este teste trava a sincronia.
    Divergir = a API protege um nome que o worker não protege (ou vice-versa)
    = a camada que devia segurar deixa passar.
    """
    worker_src = (
        Path(__file__).resolve().parents[3]
        / "worker"
        / "sessionflow_worker"
        / "infra_sessions.py"
    )
    assert worker_src.exists(), f"fonte do worker não encontrada: {worker_src}"
    text = worker_src.read_text()

    block = re.search(
        r"INFRA_SESSION_NAMES\s*=\s*frozenset\(\s*\{(.*?)\}", text, re.S
    )
    assert block, "não achei INFRA_SESSION_NAMES no worker"
    worker_names = set(re.findall(r'"([^"]+)"', block.group(1)))

    assert worker_names == set(INFRA_SESSION_NAMES), (
        "listas de infra divergem entre API e worker: "
        f"só na API={set(INFRA_SESSION_NAMES) - worker_names} "
        f"só no worker={worker_names - set(INFRA_SESSION_NAMES)}"
    )

    prefix_block = re.search(
        r'INFRA_SESSION_PREFIX\s*=\s*"([^"]+)"', text
    )
    assert prefix_block, "não achei INFRA_SESSION_PREFIX no worker"
    assert prefix_block.group(1) == INFRA_SESSION_PREFIX


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, *_args: Any, **_kwargs: Any) -> _FakeCursor:
        return self

    def __aiter__(self) -> Any:
        async def _gen() -> Any:
            for doc in self._docs:
                yield doc

        return _gen()


class _FakeCollection:
    """Coleção Mongo fake que CAPTURA a query usada na listagem."""

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs or []
        self.query: dict[str, Any] | None = None

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        self.query = query
        return _FakeCursor(self.docs)


class _FakeDb:
    def __init__(self, collection: _FakeCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> _FakeCollection:
        return self.collection


async def test_list_sessions_query_excludes_infra_names() -> None:
    """A query da listagem exclui as sessões de infra por nome EXATO.

    O regex de prefixos (sfusage-/sfmodel-/sftest-) NÃO cobre "cf" — daí o
    ``$nin`` separado. Sem ele, o túnel volta a aparecer como sessão de
    trabalho e pode ser apagado por engano.
    """
    collection = _FakeCollection()
    repo = SessionsRepository(db=_FakeDb(collection))  # type: ignore[arg-type]

    await repo.list_sessions()

    query = collection.query
    assert query is not None, "list_sessions deve consultar a coleção"
    tmux_filter = query["tmux_name"]
    assert "$nin" in tmux_filter
    assert "cf" in tmux_filter["$nin"]
    assert "sessionflow-worker" in tmux_filter["$nin"]
    assert "cloudflared-tunnel" in tmux_filter["$nin"]
    # O prefixo reservado entra pelo regex (um "$not" só — o Mongo não
    # aceita dois no mesmo campo).
    assert "$not" in tmux_filter
    assert tmux_filter["$not"].search(f"{INFRA_SESSION_PREFIX}tunnel")
    assert not tmux_filter["$not"].search("meu-projeto")
