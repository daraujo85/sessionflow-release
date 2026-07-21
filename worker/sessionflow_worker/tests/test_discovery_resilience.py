"""Robustez da reconciliação a sessões que somem (mock, sem Mongo/tmux reais).

Confirma que uma ``LibTmuxException`` em UMA sessão (que some entre o listar e
o upsert) é tolerada: o ciclo de reconciliação NÃO estoura e processa as demais
sessões válidas. Determinístico via mocks — sem o marker ``integration``.
"""

from __future__ import annotations

from libtmux.exc import LibTmuxException

from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.discovery import Discovery, ReconcileReport
from sessionflow_worker.tmux_runtime import SessionInfo


def _info(name: str) -> SessionInfo:
    return SessionInfo(
        name=name,
        id=f"${name}",
        attached=False,
        created=1700000000,
        pane_command="zsh",
        pane_pid=1234,
    )


class _FakeTmux:
    def __init__(self, infos: list[SessionInfo]) -> None:
        self._infos = infos

    def list_sessions(self) -> list[SessionInfo]:
        return list(self._infos)


class _FakeDB:
    """DB falso: ``db[collection]`` devolve um objeto-coleção inerte.

    O ``_reconcile`` faz ``self._db[self._collection]`` no início; como
    substituímos ``_upsert_session``/``_mark_missing_stopped``, a coleção em si
    nunca é usada de fato.
    """

    def __getitem__(self, _name: str) -> object:
        return object()


def _make_discovery(infos: list[SessionInfo]) -> Discovery:
    # db é tocado só por db[collection]; o resto é substituído nos testes.
    return Discovery(_FakeTmux(infos), db=_FakeDB(), host_id="test-host")  # type: ignore[arg-type]


async def test_reconcile_tolerates_session_vanishing_during_upsert() -> None:
    """Sessão somindo no upsert é pulada; as válidas seguem reconciliadas."""
    infos = [_info("alive1"), _info("ghost"), _info("alive2")]
    disc = _make_discovery(infos)

    seen: list[str] = []
    stopped_seen: list[set[str]] = []

    async def fake_upsert(info: SessionInfo, *, limits=None) -> bool:  # noqa: ANN001
        if info.name == "ghost":
            raise LibTmuxException("list-windows: can't find session: $ghost")
        seen.append(info.name)
        return True  # trata como nova p/ contabilizar em discovered

    async def fake_mark_stopped(_coll, present_names):  # noqa: ANN001
        stopped_seen.append(set(present_names))
        return 0

    disc._upsert_session = fake_upsert  # type: ignore[assignment]
    disc._mark_missing_stopped = fake_mark_stopped  # type: ignore[assignment]

    report = await disc.reconcile_once()

    assert isinstance(report, ReconcileReport)
    # As duas válidas foram processadas; a fantasma foi pulada (não estourou).
    assert seen == ["alive1", "alive2"]
    assert report.discovered == 2
    # ``present_names`` para o mark-stopped NÃO inclui a sessão que sumiu.
    assert stopped_seen and stopped_seen[0] == {"alive1", "alive2"}


async def test_reconcile_does_not_raise_when_all_vanish() -> None:
    """Se TODAS somem no upsert, reconcile retorna report vazio sem estourar."""
    disc = _make_discovery([_info("g1"), _info("g2")])

    async def fake_upsert(_info: SessionInfo, *, limits=None) -> bool:  # noqa: ANN001
        raise LibTmuxException("gone")

    async def fake_mark_stopped(_coll, present_names):  # noqa: ANN001
        return 0

    disc._upsert_session = fake_upsert  # type: ignore[assignment]
    disc._mark_missing_stopped = fake_mark_stopped  # type: ignore[assignment]

    report = await disc.reconcile_once()
    assert report == ReconcileReport(discovered=0, updated=0, stopped=0)


def test_agent_type_inference_still_works_on_info() -> None:
    # sanity: SessionInfo helper continua funcional (não quebramos o dataclass).
    assert _info("x").agent_type is AgentType.UNKNOWN


async def test_infra_sessions_never_discovered_or_stopped() -> None:
    """cloudflared-tunnel/sessionflow-worker nunca viram doc/aparecem no app.

    Incidente real: essas sessões de infra apareciam na tela de Sessões como
    qualquer sessão de trabalho e foram apagadas por engano, derrubando o
    acesso externo. Devem ser puladas SEM nem chamar ``_upsert_session``, e
    excluídas do ``present_names`` passado pro mark-stopped (não entram no
    ciclo de stopped/transição também).
    """
    infos = [
        _info("cloudflared-tunnel"),
        _info("sessionflow-worker"),
        _info("sessionflow-autoupdate"),
        _info("pvax"),
    ]
    disc = _make_discovery(infos)

    seen: list[str] = []
    stopped_seen: list[set[str]] = []

    async def fake_upsert(info: SessionInfo, *, limits=None) -> bool:  # noqa: ANN001
        seen.append(info.name)
        return True

    async def fake_mark_stopped(_coll, present_names):  # noqa: ANN001
        stopped_seen.append(set(present_names))
        return 0

    disc._upsert_session = fake_upsert  # type: ignore[assignment]
    disc._mark_missing_stopped = fake_mark_stopped  # type: ignore[assignment]

    report = await disc.reconcile_once()

    assert seen == ["pvax"]
    assert report.discovered == 1
    assert stopped_seen and stopped_seen[0] == {"pvax"}
