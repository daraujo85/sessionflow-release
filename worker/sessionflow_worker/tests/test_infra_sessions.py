"""Guarda de sessões de INFRAESTRUTURA (túnel cloudflared, worker).

Incidente real (nov/2026): a sessão ``cf`` (túnel do proxy Mongo+RabbitMQ
nos hosts Colab/Kaggle) não estava na lista de infra, então aparecia na tela
de Sessões como trabalho. A limpeza de 07/11 publicou "kill cf"; com o host
offline o comando ficou enfileirado no RabbitMQ (o TTL de 300s não cobre
``kill``) e, quando o worker voltou, MATOU O PRÓPRIO TÚNEL — o heartbeat
deixou de chegar e o host ficou travado em "Ligando" pra sempre.

Estes testes travam as duas metades da proteção:
1. a lista canônica de infra inclui "cf" (e o worker se autoprotege);
2. o consumer RECUSA matar/deletar uma sessão de infraestrutura.
"""

from __future__ import annotations

import pytest

from sessionflow_worker.command_consumer import CommandConsumer, CommandError
from sessionflow_worker.discovery import (
    INFRA_SESSION_NAMES,
    is_infra_or_internal_session,
)
from sessionflow_worker.infra_sessions import is_infra_session
from sessionflow_worker.tmux_runtime import TmuxRuntimeError


def test_cf_is_infra_session() -> None:
    """A sessão do túnel "cf" é infraestrutura — nunca sessão de trabalho."""
    assert is_infra_session("cf")
    assert "cf" in INFRA_SESSION_NAMES
    assert is_infra_or_internal_session("cf")


def test_worker_and_tunnel_names_are_infra() -> None:
    """Nomes de infra do setup (Mac/Duck e Colab/Kaggle) estão todos listados."""
    for name in ("sessionflow-worker", "cloudflared-tunnel", "cf"):
        assert is_infra_session(name), name


def test_normal_session_is_not_infra() -> None:
    assert not is_infra_session("meu-projeto")
    assert not is_infra_or_internal_session("meu-projeto")


class _FakeRuntime:
    """TmuxRuntime fake que REGISTRA as mortes (não toca em tmux real)."""

    def __init__(self) -> None:
        self.killed: list[str] = []
        self.has: set[str] = set()
        self.server = None  # CommandConsumer só repassa p/ o TmuxRuntime real

    def kill_session(self, name: str) -> None:
        self.killed.append(name)

    def has_session(self, name: str) -> bool:
        return name in self.has


class _FakeResult:
    matched_count = 1


class _FakeSessions:
    """Coleção Mongo fake: ``update_one`` só conta as chamadas."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def update_one(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls.append(args[0])
        return _FakeResult()


class _FakeDb:
    """DB fake: ``db[colecao]`` devolve a coleção fake."""

    def __init__(self) -> None:
        self.sessions = _FakeSessions()

    def __getitem__(self, _name: str) -> _FakeSessions:
        return self.sessions


def _consumer(runtime: _FakeRuntime) -> tuple[CommandConsumer, _FakeDb]:
    db = _FakeDb()
    consumer = CommandConsumer(
        channel=None,  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
        host_id="h1",
        runtime=runtime,  # type: ignore[arg-type]
    )
    return consumer, db


@pytest.mark.asyncio
async def test_kill_infra_session_is_refused() -> None:
    """``kill cf`` NÃO pode matar o túnel — recusa antes de tocar no tmux."""
    runtime = _FakeRuntime()
    consumer, _db = _consumer(runtime)

    with pytest.raises(CommandError) as exc:
        await consumer._handle_kill({"name": "cf"})

    assert "infraestrutura" in str(exc.value)
    assert runtime.killed == [], "o túnel não pode ser morto por comando"


@pytest.mark.asyncio
async def test_delete_infra_session_is_refused() -> None:
    """``delete cf`` também recusa (mata o tmux + apaga dados)."""
    runtime = _FakeRuntime()
    consumer, _db = _consumer(runtime)

    with pytest.raises(CommandError) as exc:
        await consumer._handle_delete({"name": "cf"})

    assert "infraestrutura" in str(exc.value)
    assert runtime.killed == []


@pytest.mark.asyncio
async def test_kill_normal_session_still_works() -> None:
    """A proteção NÃO pode quebrar o kill de sessão de trabalho comum."""
    runtime = _FakeRuntime()
    consumer, db = _consumer(runtime)

    result = await consumer._handle_kill({"name": "projeto-x"})

    assert result == {"name": "projeto-x"}
    assert runtime.killed == ["projeto-x"]
    assert db.sessions.calls, "o doc da sessão deve ser marcado como stopped"


def test_tmux_runtime_error_still_typed() -> None:
    """Sanidade: TmuxRuntimeError continua sendo o erro de kill."""
    assert issubclass(TmuxRuntimeError, Exception)
