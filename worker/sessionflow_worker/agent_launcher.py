"""Launcher de agente (TMUX-04, TMUX-06).

Inferência de tipo de agente a partir do comando do pane e montagem da linha
de comando (com flags de modelo e esforço) a ser enviada via ``tmux send-keys``.

Flags confirmadas via ``--help`` em 2026-06:
    - claude:   ``--model <model>``         / ``--effort <level>``
    - codex:    ``-m <model>``              / ``-c model_reasoning_effort=<level>``
      (codex não tem flag dedicada de esforço; usa override de config ``-c``;
      chave confirmada em ``~/.codex/config.toml``: ``model_reasoning_effort``)
    - gemini:   ``-m <model>``              / SEM flag de esforço (ignorado)
    - opencode: ``-m <provider/model>``     / ``--variant <level>``

Flag de permissão máxima / auto-aprovação (yolo) confirmada via ``--help``:
    - claude:   ``--permission-mode bypassPermissions``
    - codex:    ``--dangerously-bypass-approvals-and-sandbox``
    - gemini:   ``--yolo``
    - opencode: ``--dangerously-skip-permissions``

Ordem do comando montado: ``<bin> <model flags> <effort flags> <permission flag>``.
"""

from __future__ import annotations

import shlex
from enum import Enum


class AgentType(str, Enum):
    """Tipos de agente suportados."""

    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"
    OPENCODE = "opencode"
    UNKNOWN = "unknown"


# Mapeamento dos rótulos PT do mockup -> valor canônico aceito pelas CLIs.
# Observação: nem toda CLI aceita "max" (ex: codex aceita low/medium/high).
# A conversão fina por agente é feita em ``_effort_for_agent``.
EFFORT_PT_TO_CLI: dict[str, str] = {
    "Baixo": "low",
    "Médio": "medium",
    "Alto": "high",
    "Máximo": "max",
}

# Esforços válidos por agente (após normalização para o valor canônico).
# codex não suporta "max"; rebaixamos para "high" para não quebrar a config.
_CODEX_EFFORT_FALLBACK: dict[str, str] = {"max": "high"}

# Flag(s) de permissão máxima / auto-aprovação por agente (modo "yolo").
# Confirmadas via ``--help`` em 2026-06. ``unknown`` não tem entrada (sem flag).
MAX_PERMISSION_FLAGS: dict[AgentType, list[str]] = {
    AgentType.CLAUDE: ["--permission-mode", "bypassPermissions"],
    AgentType.CODEX: ["--dangerously-bypass-approvals-and-sandbox"],
    AgentType.GEMINI: ["--yolo"],
    AgentType.OPENCODE: ["--dangerously-skip-permissions"],
}


def _normalize_effort(effort: str | None) -> str | None:
    """Converte rótulo PT para valor canônico; passa-through se já canônico."""
    if effort is None:
        return None
    return EFFORT_PT_TO_CLI.get(effort, effort)


def _effort_for_agent(agent_type: AgentType, effort: str | None) -> str | None:
    """Ajusta o esforço canônico ao que o agente realmente aceita."""
    canonical = _normalize_effort(effort)
    if canonical is None:
        return None
    if agent_type is AgentType.CODEX:
        return _CODEX_EFFORT_FALLBACK.get(canonical, canonical)
    return canonical


# Tipos de agente reconhecíveis (ordem de prioridade na varredura).
_KNOWN_AGENTS: tuple[AgentType, ...] = (
    AgentType.CLAUDE,
    AgentType.CODEX,
    AgentType.GEMINI,
    AgentType.OPENCODE,
)


def infer_agent_type(pane_command: str) -> AgentType:
    """Infere o tipo de agente a partir de uma linha de comando.

    A string pode ser tanto o ``pane_current_command`` do tmux quanto a
    *cmdline completa* do processo do pane (e seus filhos) obtida via ``ps``.
    O matching é robusto: procura ``claude``/``codex``/``gemini``/``opencode``
    como **token** em qualquer posição da linha (não só no primeiro token),
    descartando o path do executável (``node .../claude`` -> ``claude``,
    ``/opt/homebrew/bin/codex`` -> ``codex``).

    Cuidado deliberado: o casamento é por token *exato* (basename), então
    ``opencode`` nunca é confundido com ``codex`` ou ``code``, e vice-versa.
    """
    if not pane_command:
        return AgentType.UNKNOWN

    try:
        tokens = shlex.split(pane_command)
    except ValueError:
        # cmdline malformada para shlex (aspas desbalanceadas etc.): cai no
        # split simples por espaço para ainda tentar reconhecer o agente.
        tokens = pane_command.split()
    if not tokens:
        return AgentType.UNKNOWN

    # Basename de cada token (descarta path do executável e de argumentos
    # tipo ``--resume claude`` não interferem pois comparamos token a token).
    binaries = {token.rsplit("/", 1)[-1] for token in tokens}

    for agent in _KNOWN_AGENTS:
        if agent.value in binaries:
            return agent

    return AgentType.UNKNOWN


def build_launch_cmd(
    agent_type: AgentType,
    model: str | None,
    effort: str | None,
    yolo: bool = True,
    resume: bool = False,
    lang_instruction: str | None = None,
) -> str:
    """Monta a linha de comando a ser enviada via ``tmux send-keys``.

    Ordem das partes: ``<bin> <model flags> <effort flags> <permission flag>``.

    - ``model None`` omite a flag de modelo (usa default da CLI).
    - gemini ignora ``effort`` (não há flag).
    - codex rebaixa ``max`` -> ``high`` (não suportado).
    - ``yolo True`` (default) acrescenta a flag de permissão máxima /
      auto-aprovação do agente (ver ``MAX_PERMISSION_FLAGS``); ``False`` omite.
    """
    if agent_type is AgentType.UNKNOWN:
        raise ValueError("não é possível montar comando para agente unknown")

    # "Default"/vazio = usar o modelo padrão do agente → OMITE a flag --model.
    # (No picker, "Default" é uma opção-rótulo, não um id válido; sem isso o
    # launch virava `--model Default` e a CLI errava no boot.)
    if model is not None and model.strip().lower() in ("", "default", "padrão", "padrao"):
        model = None

    parts: list[str] = [agent_type.value]
    # ``resume`` (usado pelo "Retomar"): continua a conversa anterior em vez de
    # começar do zero. claude/opencode têm ``--continue``; codex/gemini não têm
    # flag simples → relança novo (best-effort).
    if resume and agent_type in (AgentType.CLAUDE, AgentType.OPENCODE):
        parts += ["--continue"]
    resolved_effort = _effort_for_agent(agent_type, effort)

    if agent_type is AgentType.CLAUDE:
        if model is not None:
            parts += ["--model", model]
        if resolved_effort is not None:
            parts += ["--effort", resolved_effort]

    elif agent_type is AgentType.CODEX:
        if model is not None:
            parts += ["-m", model]
        if resolved_effort is not None:
            parts += ["-c", f"model_reasoning_effort={resolved_effort}"]

    elif agent_type is AgentType.GEMINI:
        if model is not None:
            parts += ["-m", model]
        # effort ignorado intencionalmente: gemini não tem flag de esforço.

    elif agent_type is AgentType.OPENCODE:
        if model is not None:
            parts += ["-m", model]
        if resolved_effort is not None:
            parts += ["--variant", resolved_effort]

    # Idioma: força o agente a responder no idioma escolhido SEM gastar um turno
    # nem poluir a tela (vai no system prompt). claude tem --append-system-prompt;
    # outros CLIs variam, então aplicamos só onde há suporte conhecido.
    if lang_instruction:
        if agent_type is AgentType.CLAUDE:
            parts += ["--append-system-prompt", lang_instruction]

    if yolo:
        parts += MAX_PERMISSION_FLAGS.get(agent_type, [])

    return shlex.join(parts)
