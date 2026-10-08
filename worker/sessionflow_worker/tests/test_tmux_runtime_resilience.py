"""Testes de robustez do runtime tmux a sessões que somem (sem tmux real).

Contexto
--------
Quando uma sessão tmux desaparece ENTRE o ``list-sessions`` e a inspeção das
suas janelas/panes (``list-windows``), o libtmux levanta ``LibTmuxException``
(p.ex. ``list-windows: can't find session: $370``). Antes, essa exceção subia
até o ``run()`` do runner e disparava uma RECONEXÃO COMPLETA do worker,
interrompendo a captura de output ao vivo.

Estes testes forçam a ``LibTmuxException`` de forma DETERMINÍSTICA via mocks
(não dependem de tmux/Mongo/Rabbit reais, logo NÃO têm o marker ``integration``)
e confirmam que:

- ``list_sessions`` retorna as sessões válidas mesmo quando uma some no meio;
- ``_to_info`` pula (retorna ``None``) a sessão que sumiu;
- ``pane_command``/``pane_pid``/``pane_current_path`` retornam valor neutro
  (em vez de propagar) quando a sessão não existe.
"""

from __future__ import annotations

from types import SimpleNamespace

from libtmux.exc import LibTmuxException

from sessionflow_worker.tmux_runtime import TmuxRuntime, TmuxSessionNotFoundError


class _FakePane:
    def __init__(self, command: str, pid: str, path: str) -> None:
        self.pane_current_command = command
        self.pane_pid = pid
        self.pane_current_path = path


class _FakeWindow:
    def __init__(self, pane: _FakePane) -> None:
        self.active_pane = pane


class _GoodSession:
    """Sessão saudável: expõe active_window/active_pane normalmente."""

    def __init__(self, name: str, sid: str) -> None:
        self.session_name = name
        self.session_id = sid
        self.session_attached = "0"
        self.session_created = "1700000000"
        self._pane = _FakePane("zsh", "1234", "/tmp/work")

    @property
    def active_window(self) -> _FakeWindow:
        return _FakeWindow(self._pane)


class _VanishedSession:
    """Sessão que sumiu: tocar ``active_window`` dispara LibTmuxException.

    Simula o ``list-windows: can't find session: $NNN`` que o libtmux levanta
    quando a sessão deixa de existir entre o listar e o detalhar.
    """

    def __init__(self, name: str, sid: str) -> None:
        self.session_name = name
        self.session_id = sid
        self.session_attached = "0"
        self.session_created = "1700000001"

    @property
    def active_window(self):
        raise LibTmuxException(
            f"list-windows: can't find session: {self.session_id}"
        )


def _runtime_with_sessions(sessions: list[object]) -> TmuxRuntime:
    """Monta um TmuxRuntime com um server FALSO expondo ``sessions``."""
    fake_server = SimpleNamespace(sessions=sessions)
    return TmuxRuntime(server=fake_server)  # type: ignore[arg-type]


# -- list_sessions / _to_info ------------------------------------------------


def test_list_sessions_skips_vanished_session() -> None:
    """Uma sessão somindo no meio NÃO derruba a listagem das demais válidas."""
    good = _GoodSession("alive", "$1")
    gone = _VanishedSession("ghost", "$370")
    runtime = _runtime_with_sessions([good, gone])

    infos = runtime.list_sessions()

    names = {i.name for i in infos}
    assert names == {"alive"}  # a fantasma foi pulada
    assert "ghost" not in names
    # A válida veio completa (pane inspecionado com sucesso).
    alive = next(i for i in infos if i.name == "alive")
    assert alive.pane_command == "zsh"
    assert alive.pane_pid == 1234


def test_list_sessions_returns_valid_when_first_vanishes() -> None:
    """Ordem não importa: fantasma primeiro, válidas depois, todas retornadas."""
    gone = _VanishedSession("ghost", "$999")
    good_a = _GoodSession("a", "$2")
    good_b = _GoodSession("b", "$3")
    runtime = _runtime_with_sessions([gone, good_a, good_b])

    infos = runtime.list_sessions()

    assert {i.name for i in infos} == {"a", "b"}


def test_to_info_returns_none_for_vanished_session() -> None:
    """``_to_info`` retorna None (sinal de "pule") p/ sessão que sumiu."""
    runtime = _runtime_with_sessions([])
    assert runtime._to_info(_VanishedSession("ghost", "$5")) is None


def test_to_info_builds_info_for_good_session() -> None:
    runtime = _runtime_with_sessions([])
    info = runtime._to_info(_GoodSession("alive", "$1"))
    assert info is not None
    assert info.name == "alive"
    assert info.pane_command == "zsh"


def test_list_sessions_empty_when_server_listing_fails() -> None:
    """Se o próprio ``list-sessions`` falha, retorna [] em vez de propagar."""

    class _ExplodingSessions:
        def __iter__(self):
            raise LibTmuxException("list-sessions: no server running")

    runtime = _runtime_with_sessions(_ExplodingSessions())  # type: ignore[arg-type]
    assert runtime.list_sessions() == []


# -- pane_* / has_session ----------------------------------------------------


def _server_get_raises() -> TmuxRuntime:
    """Server cujo ``sessions.get`` levanta LibTmuxException (sessão sumiu)."""

    def _get(*_args, **_kwargs):
        raise LibTmuxException("can't find session")

    fake_sessions = SimpleNamespace(get=_get)
    fake_server = SimpleNamespace(sessions=fake_sessions)
    return TmuxRuntime(server=fake_server)  # type: ignore[arg-type]


def test_pane_command_empty_when_session_gone() -> None:
    assert _server_get_raises().pane_command("ghost") == ""


def test_pane_pid_none_when_session_gone() -> None:
    assert _server_get_raises().pane_pid("ghost") is None


def test_pane_current_path_empty_when_session_gone() -> None:
    assert _server_get_raises().pane_current_path("ghost") == ""


def test_pane_command_empty_when_window_vanishes_midway() -> None:
    """Sessão existe no get, mas some ao tocar active_window -> '' (não estoura)."""

    gone = _VanishedSession("ghost", "$7")
    fake_sessions = SimpleNamespace(get=lambda *a, **k: gone)
    runtime = TmuxRuntime(server=SimpleNamespace(sessions=fake_sessions))  # type: ignore[arg-type]
    assert runtime.pane_command("ghost") == ""
    assert runtime.pane_pid("ghost") is None
    assert runtime.pane_current_path("ghost") == ""


def test_has_session_false_when_server_raises() -> None:
    def _has(*_a, **_k):
        raise LibTmuxException("boom")

    runtime = TmuxRuntime(server=SimpleNamespace(has_session=_has))  # type: ignore[arg-type]
    assert runtime.has_session("ghost") is False


def test_get_session_raises_typed_error_on_libtmux_exc() -> None:
    """Lookup por nome explícito vira TmuxSessionNotFoundError (tipado)."""
    runtime = _server_get_raises()
    try:
        runtime._get_session("ghost")
    except TmuxSessionNotFoundError:
        pass
    else:  # pragma: no cover - falha explícita se não levantar
        raise AssertionError("esperava TmuxSessionNotFoundError")
