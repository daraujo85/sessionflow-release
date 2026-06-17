"""Testes do launcher de agente (TMUX-04, TMUX-06)."""

from sessionflow_worker.agent_launcher import (
    AgentType,
    build_launch_cmd,
    infer_agent_type,
)


# --- build_launch_cmd: 1 por agente, com flags corretas ---
# Nota: yolo=True é o default -> a flag de permissão máxima entra por padrão.


def test_build_claude_model_and_effort() -> None:
    cmd = build_launch_cmd(AgentType.CLAUDE, "opus-4", "Alto")
    assert cmd == (
        "claude --model opus-4 --effort high "
        "--permission-mode bypassPermissions"
    )


def test_build_codex_model_and_effort() -> None:
    # codex usa override de config -c model_reasoning_effort=<level>.
    cmd = build_launch_cmd(AgentType.CODEX, "gpt-5-codex", "Médio")
    assert cmd == (
        "codex -m gpt-5-codex -c model_reasoning_effort=medium "
        "--dangerously-bypass-approvals-and-sandbox"
    )


def test_build_codex_max_falls_back_to_high() -> None:
    # codex não suporta "max"; rebaixa para "high".
    cmd = build_launch_cmd(AgentType.CODEX, None, "Máximo")
    assert cmd == (
        "codex -c model_reasoning_effort=high "
        "--dangerously-bypass-approvals-and-sandbox"
    )


def test_build_gemini_ignores_effort() -> None:
    # gemini não tem flag de esforço: effort é ignorado mesmo se passado.
    cmd = build_launch_cmd(AgentType.GEMINI, "gemini-2.5-pro", "Alto")
    assert cmd == "gemini -m gemini-2.5-pro --yolo"


def test_build_opencode_model_and_variant() -> None:
    cmd = build_launch_cmd(
        AgentType.OPENCODE, "anthropic/claude-opus", "Máximo"
    )
    assert cmd == (
        "opencode -m anthropic/claude-opus --variant max "
        "--dangerously-skip-permissions"
    )


def test_build_model_none_omits_model_flag() -> None:
    # model None -> sem flag de modelo (usa default da CLI).
    cmd = build_launch_cmd(AgentType.CLAUDE, None, "Baixo")
    assert cmd == "claude --effort low --permission-mode bypassPermissions"


def test_build_no_model_no_effort_is_just_permission_flag() -> None:
    # Sem model/effort, sobra apenas a flag de permissão máxima (yolo default).
    cmd = build_launch_cmd(AgentType.CLAUDE, None, None)
    assert cmd == "claude --permission-mode bypassPermissions"


def test_build_unknown_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_launch_cmd(AgentType.UNKNOWN, None, None)


# --- yolo (permissão máxima): default por agente + toggle off ---


def test_build_claude_yolo_default_adds_permission_flag() -> None:
    cmd = build_launch_cmd(AgentType.CLAUDE, None, None)
    assert "--permission-mode bypassPermissions" in cmd


def test_build_codex_yolo_default_adds_permission_flag() -> None:
    cmd = build_launch_cmd(AgentType.CODEX, None, None)
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_build_gemini_yolo_default_adds_permission_flag() -> None:
    cmd = build_launch_cmd(AgentType.GEMINI, None, None)
    assert cmd == "gemini --yolo"


def test_build_opencode_yolo_default_adds_permission_flag() -> None:
    cmd = build_launch_cmd(AgentType.OPENCODE, None, None)
    assert cmd == "opencode --dangerously-skip-permissions"


def test_build_yolo_false_omits_permission_flag_claude() -> None:
    cmd = build_launch_cmd(AgentType.CLAUDE, "opus-4", "Alto", yolo=False)
    assert cmd == "claude --model opus-4 --effort high"


def test_build_yolo_false_omits_permission_flag_codex() -> None:
    cmd = build_launch_cmd(AgentType.CODEX, "gpt-5-codex", "Médio", yolo=False)
    assert cmd == "codex -m gpt-5-codex -c model_reasoning_effort=medium"


def test_build_yolo_false_omits_permission_flag_gemini() -> None:
    cmd = build_launch_cmd(AgentType.GEMINI, "gemini-2.5-pro", None, yolo=False)
    assert cmd == "gemini -m gemini-2.5-pro"


def test_build_yolo_false_omits_permission_flag_opencode() -> None:
    cmd = build_launch_cmd(
        AgentType.OPENCODE, None, None, yolo=False
    )
    assert cmd == "opencode"


# --- infer_agent_type: 4 conhecidos + unknown ---


def test_infer_claude() -> None:
    assert infer_agent_type("claude --model opus-4") is AgentType.CLAUDE


def test_infer_codex() -> None:
    assert infer_agent_type("codex exec") is AgentType.CODEX


def test_infer_gemini_with_path() -> None:
    # descarta o path do executável.
    assert infer_agent_type("/usr/local/bin/gemini -p hi") is AgentType.GEMINI


def test_infer_opencode() -> None:
    assert infer_agent_type("opencode run hello") is AgentType.OPENCODE


def test_infer_unknown() -> None:
    assert infer_agent_type("bash -lc vim") is AgentType.UNKNOWN


def test_infer_empty_is_unknown() -> None:
    assert infer_agent_type("") is AgentType.UNKNOWN


# --- infer_agent_type: cmdline REAL (agente como filho do shell) ---
# claude é um CLI Node: o pane mostra 'node', mas a cmdline do processo é
# 'claude ...'. A inferência precisa achar o token em qualquer posição.


def test_infer_claude_real_resume_cmdline() -> None:
    cmd = "claude --permission-mode bypassPermissions --resume planner"
    assert infer_agent_type(cmd) is AgentType.CLAUDE


def test_infer_claude_node_wrapper_path() -> None:
    cmd = "node /usr/local/lib/node_modules/.bin/claude --resume portal"
    assert infer_agent_type(cmd) is AgentType.CLAUDE


def test_infer_codex_absolute_path() -> None:
    assert infer_agent_type("/opt/homebrew/bin/codex") is AgentType.CODEX


def test_infer_codex_exec_with_path() -> None:
    cmd = "/opt/homebrew/bin/codex exec -m gpt-5-codex"
    assert infer_agent_type(cmd) is AgentType.CODEX


def test_infer_opencode_not_confused_with_codex() -> None:
    # 'opencode' contém 'code' mas NÃO deve casar como codex/code.
    assert infer_agent_type("/usr/bin/opencode run hi") is AgentType.OPENCODE


def test_infer_codex_not_matched_by_opencode_token() -> None:
    assert infer_agent_type("opencode") is not AgentType.CODEX


def test_infer_multiline_cmdline_picks_agent_token() -> None:
    # cmdline concatenada de pane(shell) + filho(claude) + neto(mcp).
    cmd = (
        "-zsh\n"
        "claude --permission-mode bypassPermissions --resume pratinha\n"
        "npm exec @upstash/context7-mcp"
    )
    assert infer_agent_type(cmd) is AgentType.CLAUDE


def test_infer_shell_only_is_unknown() -> None:
    assert infer_agent_type("-zsh") is AgentType.UNKNOWN
    assert infer_agent_type("/bin/bash") is AgentType.UNKNOWN


def test_infer_claude_priority_over_arg_named_codex() -> None:
    # sessão chamada 'codex' resumida pelo claude: claude vence (prioridade).
    cmd = "claude --resume codex"
    assert infer_agent_type(cmd) is AgentType.CLAUDE


def test_infer_malformed_quotes_falls_back_to_split() -> None:
    # aspas desbalanceadas não devem explodir; ainda reconhece o agente.
    assert infer_agent_type('claude --resume "planner') is AgentType.CLAUDE
