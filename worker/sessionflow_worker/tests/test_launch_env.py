"""Scroll nativo: o agente precisa nascer com ``SESSION_ENV`` (sem alt-screen)
mesmo quando o pane SOBREVIVE a um restart/resume — aí o env do
``new_session`` não vale (shell já existia), então vai prefixado na linha."""

from __future__ import annotations

from sessionflow_worker.command_consumer import _with_session_env


def test_prefixa_env_na_linha_de_lancamento() -> None:
    cmd = _with_session_env("claude --resume abc")
    assert cmd == "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1 claude --resume abc"
