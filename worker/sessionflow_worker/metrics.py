"""Métricas REAIS de sessões Claude, lidas dos JSONL do Claude Code.

O Claude Code grava cada turno de uma sessão em
``~/.claude/projects/<cwd-encoded>/<session-uuid>.jsonl``, onde ``<cwd-encoded>``
é o ``cwd`` absoluto com **todos** os ``/`` e ``.`` trocados por ``-``
(ex.: ``/Users/diego/projects/pvax`` -> ``-Users-diego-Documents-projects-pvax``).

Cada linha é um JSON; as linhas do assistant trazem ``message.usage`` com:
``input_tokens``, ``output_tokens``, ``cache_read_input_tokens`` e
``cache_creation_input_tokens``, além de ``message.model`` (ex. ``claude-opus-4-8``).

Este módulo é **somente leitura**: localiza o JSONL mais recente da sessão e
deriva um dicionário de métricas. Qualquer falha degrada para ``None`` (o front
mostra "—"). Nunca escreve em ``~/.claude``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("sessionflow_worker.metrics")

#: Raiz padrão dos projetos do Claude Code. Injetável em testes.
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

#: Janela de contexto padrão (tokens). Conservador: a maioria dos modelos
#: Claude expõe 200k de contexto. A variante de 1M só é usada quando o id do
#: modelo deixa explícito (substring "1m"/"1000k") — ver ``_context_max_for``.
DEFAULT_CONTEXT_MAX = 200_000
CONTEXT_MAX_1M = 1_000_000

#: Mapeamento id-cru -> rótulo amigável (best-effort). Quando não há match,
#: usa-se o id cru do modelo.
_MODEL_LABELS = {
    "claude-opus-4-8": "Opus 4.8",
    "claude-opus-4-7": "Opus 4.7",
    "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-4-5": "Sonnet 4.5",
    "claude-haiku-4-5": "Haiku 4.5",
}


def _claude_activity(stats_path: Path) -> dict | None:
    """Atividade REAL do Claude a partir de ``~/.claude/stats-cache.json``.

    Esse arquivo é GLOBAL (todas as sessões) e tem ``dailyActivity`` com
    ``{date, messageCount, sessionCount, toolCallCount}`` por dia. Computamos a
    atividade do dia mais recente e o acumulado dos últimos 7 dias. Não há %
    de limite (isso é server-side) — isto é uso real medido localmente.
    Retorna ``None`` se o arquivo/dados não existirem.
    """
    try:
        data = json.loads(stats_path.read_text())
        days = data.get("dailyActivity") or []
        if not days:
            return None
        days = sorted(days, key=lambda d: d.get("date", ""))
        last = days[-1]
        week = days[-7:]
        return {
            "today_messages": int(last.get("messageCount", 0) or 0),
            "today_tools": int(last.get("toolCallCount", 0) or 0),
            "today_date": last.get("date"),
            "week_messages": sum(int(d.get("messageCount", 0) or 0) for d in week),
            "week_tools": sum(int(d.get("toolCallCount", 0) or 0) for d in week),
        }
    except Exception:  # noqa: BLE001 - best-effort
        return None


def _encode_work_dir(work_dir: str) -> str:
    """Codifica um work_dir absoluto no nome de pasta usado pelo Claude Code.

    O Claude Code troca **todo caractere não-alfanumérico** por ``-`` (não só
    ``/`` e ``.``: também ``_``, espaços, etc.). Ex.: ``/Users/d/projects/prata_digital``
    → ``-Users-d-projects-prata-digital``. Expande ``~`` antes (o tmux_runtime
    expõe a forma colapsada).
    """
    abs_path = str(Path(work_dir).expanduser().resolve(strict=False))
    return re.sub(r"[^a-zA-Z0-9]", "-", abs_path)


def _model_label(model_id: str) -> str:
    """Rótulo amigável para um id de modelo, ou o id cru se desconhecido."""
    if not model_id:
        return model_id
    # Tenta match exato; senão tenta por prefixo conhecido.
    if model_id in _MODEL_LABELS:
        return _MODEL_LABELS[model_id]
    for prefix, label in _MODEL_LABELS.items():
        if model_id.startswith(prefix):
            return label
    return model_id


def _context_max_for(model_id: str) -> int:
    """Infere a janela de contexto a partir do id do modelo.

    Heurística **conservadora**: só retorna 1.000.000 quando o id do modelo
    indica explicitamente a variante de 1M (substring ``1m`` ou ``1000k``, p.ex.
    ``claude-opus-4-8[1m]``). Caso contrário, assume o padrão de 200.000.

    Observação: o ``message.model`` gravado no JSONL normalmente **não** carrega
    o sufixo ``[1m]`` — nesses casos caímos no padrão de 200k de propósito, para
    não inflar o denominador e mostrar uma % de contexto enganosamente baixa.
    """
    low = (model_id or "").lower()
    if "1m" in low or "1000k" in low:
        return CONTEXT_MAX_1M
    return DEFAULT_CONTEXT_MAX


def _latest_jsonl(project_dir: Path) -> Path | None:
    """JSONL mais recentemente modificado no dir do projeto (sessão ativa)."""
    try:
        candidates = [
            p for p in project_dir.iterdir() if p.is_file() and p.suffix == ".jsonl"
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _iter_usage_lines(jsonl_path: Path):
    """Itera os pares ``(usage, model)`` das linhas com ``message.usage``.

    Linhas inválidas (JSON quebrado / sem usage) são ignoradas. ``model`` pode
    vir ``None`` quando a linha não o expõe.
    """
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            yield usage, message.get("model")


def claude_metrics_for(
    work_dir: str,
    projects_root: str | os.PathLike[str] | None = None,
) -> dict | None:
    """Métricas REAIS da sessão Claude cujo ``cwd`` é ``work_dir``.

    Encontra ``<projects_root>/<encode(work_dir)>``, pega o JSONL mais
    recentemente modificado (sessão ativa) e computa o dicionário ``metrics``:

    - ``model``: rótulo amigável (ou id cru) do ``message.model`` da última
      linha de usage (último turno do assistant).
    - ``context_used``: ``input + cache_read + cache_creation`` da última linha
      (tokens "vivos" na janela de contexto).
    - ``context_max``: inferido do modelo (ver ``_context_max_for``).
    - ``context_pct``: ``round(context_used / context_max * 100)``.
    - ``tokens_in``: igual a ``context_used`` (entrada atual da janela).
    - ``tokens_out``: SOMA de ``output_tokens`` de TODAS as linhas (saída
      acumulada da sessão).
    - ``source``: ``"claude_jsonl"``.

    Retorna ``None`` se o dir/JSONL/usage não existir ou em qualquer falha
    (degrada graciosamente). Apenas leitura.
    """
    try:
        root = Path(projects_root) if projects_root is not None else DEFAULT_PROJECTS_ROOT
        project_dir = root / _encode_work_dir(work_dir)
        if not project_dir.is_dir():
            return None

        jsonl_path = _latest_jsonl(project_dir)
        if jsonl_path is None:
            return None

        last_usage: dict | None = None
        last_model: str | None = None
        tokens_out_total = 0
        saw_usage = False

        for usage, model in _iter_usage_lines(jsonl_path):
            saw_usage = True
            last_usage = usage
            if model:
                last_model = model
            tokens_out_total += int(usage.get("output_tokens", 0) or 0)

        if not saw_usage or last_usage is None:
            return None

        context_used = (
            int(last_usage.get("input_tokens", 0) or 0)
            + int(last_usage.get("cache_read_input_tokens", 0) or 0)
            + int(last_usage.get("cache_creation_input_tokens", 0) or 0)
        )
        model_id = last_model or ""
        context_max = _context_max_for(model_id)
        # Inferência: se o uso já passou do default (200k), a janela é maior
        # (o jsonl nem sempre carrega o sufixo "[1m]"). Promove para 1M para
        # não exibir % > 100. Cap final em 100% por segurança.
        if context_used > context_max:
            context_max = 1_000_000
        context_pct = (
            min(100, round(context_used / context_max * 100)) if context_max else 0
        )

        return {
            "model": _model_label(model_id) if model_id else None,
            "context_used": context_used,
            "context_max": context_max,
            "context_pct": context_pct,
            "tokens_in": context_used,
            "tokens_out": tokens_out_total,
            "activity": _claude_activity(root.parent / "stats-cache.json"),
            "source": "claude_jsonl",
        }
    except Exception:  # noqa: BLE001 - leitura best-effort, nunca estoura
        logger.debug(
            "claude_metrics_for falhou para work_dir=%r", work_dir, exc_info=True
        )
        return None
