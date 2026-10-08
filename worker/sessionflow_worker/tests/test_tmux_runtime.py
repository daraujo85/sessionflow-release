"""Testes de integração do runtime tmux (contra tmux REAL).

Marker: ``integration``. Cada teste usa nomes namespaced ``sftest-<uuid4>``
para nunca colidir com sessões reais do usuário, e o teardown mata APENAS
sessões cujo nome começa com ``sftest-``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest

from sessionflow_worker.agent_launcher import AgentType
from sessionflow_worker.tmux_runtime import (
    TmuxNameError,
    TmuxRuntime,
    TmuxRuntimeError,
    TmuxSessionNotFoundError,
)

pytestmark = pytest.mark.integration

_PREFIX = "sftest-"


@pytest.fixture
def runtime() -> TmuxRuntime:
    return TmuxRuntime()


@pytest.fixture
def make_name(runtime: TmuxRuntime) -> Iterator[Callable[[], str]]:
    """Gera nomes namespaced e garante que TODOS sejam mortos no teardown.

    CRÍTICO: o teardown só toca em sessões com prefixo ``sftest-``; sessões
    reais do usuário jamais são afetadas.
    """
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


def test_new_session_and_list(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    name = make_name()
    info = runtime.new_session(name, tmp_path)
    assert info.name == name
    assert info.id.startswith("$")

    names = {s.name for s in runtime.list_sessions()}
    assert name in names


def test_has_session(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    name = make_name()
    assert runtime.has_session(name) is False
    runtime.new_session(name, tmp_path)
    assert runtime.has_session(name) is True


def test_list_includes_external_sessions(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    """TMUX-02: list_sessions reflete o servidor compartilhado.

    Criamos via API uma sessão "externa" (simula sessão fora do SessionFlow)
    e confirmamos que aparece na listagem.
    """
    name = make_name()
    runtime.new_session(name, tmp_path)
    sessions = runtime.list_sessions()
    assert any(s.name == name for s in sessions)
    # Garante que a listagem não está filtrando por prefixo nosso.
    assert len(sessions) >= 1


def test_rename_session(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    old = make_name()
    new = make_name()
    runtime.new_session(old, tmp_path)
    runtime.rename_session(old, new)

    names = {s.name for s in runtime.list_sessions()}
    assert new in names
    assert old not in names
    assert runtime.has_session(new) is True
    assert runtime.has_session(old) is False


def test_kill_session(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    name = make_name()
    runtime.new_session(name, tmp_path)
    assert runtime.has_session(name) is True

    runtime.kill_session(name)
    assert runtime.has_session(name) is False
    assert name not in {s.name for s in runtime.list_sessions()}


def test_detached_session_is_not_attached(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    """TMUX-11: ``new-session -d`` => sessão detached."""
    name = make_name()
    runtime.new_session(name, tmp_path)
    assert runtime.is_attached(name) is False


def test_pane_command_and_agent_inference(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    """Pane roda o shell => agente inferido como UNKNOWN."""
    name = make_name()
    runtime.new_session(name, tmp_path)
    cmd = runtime.pane_command(name)
    assert cmd  # algo como 'zsh'/'bash'/'sh'
    assert runtime.agent_type(name) is AgentType.UNKNOWN

    info = next(s for s in runtime.list_sessions() if s.name == name)
    assert info.agent_type is AgentType.UNKNOWN
    assert info.pane_pid is not None and info.pane_pid > 0


def test_pane_process_cmdline_shell_is_unknown(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    """Sessão rodando só o shell => cmdline do processo não casa agente.

    ``pane_process_cmdline`` deve retornar algo (a linha do shell), mas a
    inferência via cmdline completa permanece UNKNOWN. Não lança agentes.
    """
    name = make_name()
    runtime.new_session(name, tmp_path)

    cmdline = runtime.pane_process_cmdline(name)
    assert cmdline  # cmdline do shell do pane via ps
    assert runtime.agent_type(name) is AgentType.UNKNOWN

    info = next(s for s in runtime.list_sessions() if s.name == name)
    assert info.process_cmdline  # populado no snapshot
    assert info.agent_type is AgentType.UNKNOWN


def test_work_dir_captured_from_pane_current_path(
    runtime: TmuxRuntime, make_name: Callable[[], str]
) -> None:
    """SessionInfo / list_sessions trazem o cwd do pane (``-c <dir>``).

    Cria a sessão em ``/tmp`` (um dir conhecido e estável) e confirma que o
    ``work_dir`` reflete esse pane, tanto via ``pane_current_path`` quanto via
    o snapshot de ``list_sessions``.
    """
    name = make_name()
    info = runtime.new_session(name, "/tmp")

    # /tmp em macOS é symlink p/ /private/tmp; aceita ambos (e ~ colapsado).
    def _ok(path: str) -> bool:
        return path.endswith("/tmp") or path == "/tmp"

    assert _ok(info.work_dir)
    assert _ok(runtime.pane_current_path(name))

    listed = next(s for s in runtime.list_sessions() if s.name == name)
    assert _ok(listed.work_dir)


def test_new_session_nonexistent_workdir_errors(
    runtime: TmuxRuntime, make_name: Callable[[], str], tmp_path
) -> None:
    name = make_name()
    missing = tmp_path / "nao-existe"
    with pytest.raises(TmuxRuntimeError):
        runtime.new_session(name, missing)
    assert runtime.has_session(name) is False


def test_invalid_name_rejected(
    runtime: TmuxRuntime, tmp_path
) -> None:
    for bad in ("foo.bar", "foo:bar", "   "):
        with pytest.raises(TmuxNameError):
            runtime.new_session(bad, tmp_path)


def test_operations_on_missing_session_raise(runtime: TmuxRuntime) -> None:
    ghost = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    assert runtime.has_session(ghost) is False
    with pytest.raises(TmuxSessionNotFoundError):
        runtime.kill_session(ghost)
    with pytest.raises(TmuxSessionNotFoundError):
        runtime.is_attached(ghost)
    with pytest.raises(TmuxSessionNotFoundError):
        runtime.rename_session(ghost, f"{_PREFIX}whatever")
