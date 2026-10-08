"""Tabela-verdade da derivação de estado (TMUX-12)."""

from sessionflow_worker.state import SessionState, derive_state


def test_tmux_absent_is_stopped() -> None:
    # tmux ausente vence tudo, mesmo com sinais "vivos".
    assert derive_state(False, True, True, None) is SessionState.STOPPED


def test_tmux_absent_with_error_exit_still_stopped() -> None:
    # Precedência: stopped > error.
    assert derive_state(False, True, True, 1) is SessionState.STOPPED


def test_error_when_nonzero_exit() -> None:
    assert derive_state(True, True, False, 1) is SessionState.ERROR


def test_error_takes_precedence_over_detached() -> None:
    # exit != 0 vence detached, mesmo sem anexar.
    assert derive_state(True, False, False, 2) is SessionState.ERROR


def test_exit_code_zero_is_not_error() -> None:
    # Edge: sucesso (0) não é erro.
    assert derive_state(True, True, True, 0) is SessionState.RUNNING


def test_exit_code_none_is_not_error() -> None:
    # Edge: agente ainda rodando, exit_code None.
    assert derive_state(True, True, True, None) is SessionState.RUNNING


def test_running_when_alive_even_if_not_attached() -> None:
    # SessionFlow monitora sem TTY: agente vivo = running, mesmo sem cliente.
    assert derive_state(True, False, True, None) is SessionState.RUNNING


def test_detached_when_not_attached_and_no_agent() -> None:
    # Sem agente vivo e ninguém anexado = sessão tmux ociosa (detached).
    assert derive_state(True, False, False, None) is SessionState.DETACHED


def test_running_when_attached_and_alive() -> None:
    assert derive_state(True, True, True, None) is SessionState.RUNNING
