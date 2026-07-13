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
import time
import urllib.request
from datetime import datetime, timedelta, timezone
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

#: Preços de API em USD por MTok: (substring do id do modelo, input, output).
#: Matching por substring case-insensitive; primeiro match vence. cache_read
#: custa 10% do input e cache_write 125% do input (padrão Anthropic). Modelos
#: sem match (fable/mythos/ids desconhecidos) ficam SEM preço (usd=None) —
#: nunca inventamos valor. Fácil de editar quando a tabela mudar.
_PRICES_USD_PER_MTOK: list[tuple[str, float, float]] = [
    ("opus", 15.0, 75.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 1.0, 5.0),
]
_CACHE_READ_FACTOR = 0.10
_CACHE_WRITE_FACTOR = 1.25


def _price_for(model_id: str) -> tuple[float, float] | None:
    """(input, output) USD/MTok pro modelo, ou ``None`` se desconhecido."""
    low = (model_id or "").lower()
    for needle, price_in, price_out in _PRICES_USD_PER_MTOK:
        if needle in low:
            return price_in, price_out
    return None


def _cost_from_usage(by_model: dict[str, dict[str, int]]) -> dict:
    """Custo estimado (USD, preço de API) a partir dos tokens POR MODELO.

    ``by_model`` mapeia id/rótulo do modelo -> contadores ``input``/``output``/
    ``cache_read``/``cache_write``. Retorna ``{"total_usd", "by_model": [...]}``
    com as entradas ordenadas por usd desc (sem preço por último). Modelos sem
    preço mapeado entram com ``usd: None`` (os tokens são reportados mesmo
    assim); ``total_usd`` soma só os conhecidos, ou ``None`` se nenhum tiver
    preço.
    """
    entries: list[dict] = []
    total: float | None = None
    for model, tok in by_model.items():
        # Ignora buckets sem token algum (ex.: linhas "<synthetic>" do Claude
        # Code, que têm usage zerado) — só poluiriam a quebra por modelo.
        if not any(tok.values()):
            continue
        price = _price_for(model)
        usd: float | None = None
        if price is not None:
            price_in, price_out = price
            usd = (
                tok["input"] * price_in
                + tok["output"] * price_out
                + tok["cache_read"] * price_in * _CACHE_READ_FACTOR
                + tok["cache_write"] * price_in * _CACHE_WRITE_FACTOR
            ) / 1_000_000
            usd = round(usd, 4)
            total = (total or 0.0) + usd
        entries.append(
            {
                "model": model,
                "input": tok["input"],
                "output": tok["output"],
                "cache_read": tok["cache_read"],
                "cache_write": tok["cache_write"],
                "usd": usd,
            }
        )
    entries.sort(key=lambda e: (e["usd"] is None, -(e["usd"] or 0.0)))
    rate = _usd_brl_rate()
    total_usd = round(total, 4) if total is not None else None
    return {
        "total_usd": total_usd,
        # Cotação USD→BRL do dia (cache ~6h; fallback env) e o total convertido —
        # o front mostra os dois. Ausentes (None) quando a cotação não veio.
        "brl_rate": rate,
        "total_brl": round(total_usd * rate, 2)
        if total_usd is not None and rate is not None
        else None,
        "by_model": entries,
    }


# Cache em memória da cotação USD→BRL: (valor, monotonic da leitura).
_BRL_CACHE: list = [None, 0.0]
_BRL_TTL_S = 6 * 3600.0


def _usd_brl_rate() -> float | None:
    """Cotação USD→BRL do dia (AwesomeAPI, grátis/sem chave), cache ~6h.

    Fallback: env ``SESSIONFLOW_USD_BRL`` (câmbio fixo). Sem nada → ``None``
    (o front mostra só USD). Best-effort: nunca levanta.
    """
    now = time.monotonic()
    if _BRL_CACHE[0] is not None and now - _BRL_CACHE[1] < _BRL_TTL_S:
        return _BRL_CACHE[0]
    rate: float | None = None
    try:
        req = urllib.request.Request(
            "https://economia.awesomeapi.com.br/json/last/USD-BRL",
            headers={"User-Agent": "sessionflow-worker"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rate = round(float(data["USDBRL"]["bid"]), 4)
    except Exception:  # noqa: BLE001 - best-effort; cai pro fallback
        try:
            env = os.environ.get("SESSIONFLOW_USD_BRL", "")
            rate = float(env) if env else None
        except ValueError:
            rate = None
    if rate is not None:
        _BRL_CACHE[0] = rate
        _BRL_CACHE[1] = now
    return rate


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
    """Itera as tuplas ``(usage, model, ts)`` das linhas com ``message.usage``.

    Linhas inválidas (JSON quebrado / sem usage) são ignoradas. ``model`` pode
    vir ``None`` quando a linha não o expõe. ``ts`` é o ``timestamp`` ISO8601
    gravado pelo Claude Code (UTC, ex. ``2026-06-22T23:43:57.358Z``) já
    parseado, ou ``None`` se ausente/inválido (usado só p/ os filtros de
    período — a soma "sempre" não depende dele).
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
            ts: datetime | None = None
            raw_ts = obj.get("timestamp")
            if isinstance(raw_ts, str):
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    ts = None
            yield usage, message.get("model"), ts


#: Janelas ROLANTES (não calendário) usadas pelo filtro "hoje/semana/mês" do
#: Top 3 da Home — mais simples e sem ambiguidade de fuso/início de semana.
_PERIOD_WINDOWS = {
    "today": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}


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
    - ``cost``: custo estimado em USD (preço de API) POR MODELO —
      ``{"total_usd", "by_model": [{model, input, output, cache_read,
      cache_write, usd}]}`` — ou ``None`` em falha (best-effort).
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
        # Tokens acumulados POR MODELO (sessão pode trocar de modelo no meio).
        # Chave = rótulo amigável (ou id cru); linha sem model cai no último
        # modelo visto (fallback: modelo corrente da sessão) ou "desconhecido".
        usage_by_model: dict[str, dict[str, int]] = {}
        # Mesma coisa, mas UM balde por janela de período (hoje/semana/mês) —
        # só recebe a linha se ela cair dentro da janela. Usado pelos filtros
        # do Top 3 da Home; "sempre" reaproveita usage_by_model acima.
        now = datetime.now(timezone.utc)
        period_usage: dict[str, dict[str, dict[str, int]]] = {
            key: {} for key in _PERIOD_WINDOWS
        }

        for usage, model, ts in _iter_usage_lines(jsonl_path):
            saw_usage = True
            last_usage = usage
            if model:
                last_model = model
            tokens_out_total += int(usage.get("output_tokens", 0) or 0)
            try:
                key = _model_label(model or last_model or "") or "desconhecido"
                bucket = usage_by_model.setdefault(
                    key,
                    {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                )
                bucket["input"] += int(usage.get("input_tokens", 0) or 0)
                bucket["output"] += int(usage.get("output_tokens", 0) or 0)
                bucket["cache_read"] += int(
                    usage.get("cache_read_input_tokens", 0) or 0
                )
                bucket["cache_write"] += int(
                    usage.get("cache_creation_input_tokens", 0) or 0
                )
                if ts is not None:
                    for period, window in _PERIOD_WINDOWS.items():
                        if now - ts <= window:
                            pb = period_usage[period].setdefault(
                                key,
                                {
                                    "input": 0,
                                    "output": 0,
                                    "cache_read": 0,
                                    "cache_write": 0,
                                },
                            )
                            pb["input"] += int(usage.get("input_tokens", 0) or 0)
                            pb["output"] += int(usage.get("output_tokens", 0) or 0)
                            pb["cache_read"] += int(
                                usage.get("cache_read_input_tokens", 0) or 0
                            )
                            pb["cache_write"] += int(
                                usage.get("cache_creation_input_tokens", 0) or 0
                            )
            except Exception:  # noqa: BLE001 - custo é best-effort
                pass

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

        # Custo estimado por modelo (best-effort: falha some, não quebra o resto).
        cost: dict | None = None
        try:
            if usage_by_model:
                cost = _cost_from_usage(usage_by_model)
        except Exception:  # noqa: BLE001
            cost = None

        # Mesmo cálculo, por janela de período — alimenta o filtro
        # hoje/semana/mês do Top 3 da Home. "sempre" usa tokens_in/tokens_out/
        # cost acima (não duplicado aqui).
        tokens_periods: dict[str, dict] = {}
        for period, by_model in period_usage.items():
            tokens_in_p = sum(b["input"] for b in by_model.values())
            tokens_out_p = sum(b["output"] for b in by_model.values())
            cost_p: dict | None = None
            try:
                if by_model:
                    cost_p = _cost_from_usage(by_model)
            except Exception:  # noqa: BLE001
                cost_p = None
            tokens_periods[period] = {
                "tokens_in": tokens_in_p,
                "tokens_out": tokens_out_p,
                "cost": cost_p,
            }

        return {
            "model": _model_label(model_id) if model_id else None,
            "cost": cost,
            "context_used": context_used,
            "context_max": context_max,
            "context_pct": context_pct,
            "tokens_in": context_used,
            "tokens_out": tokens_out_total,
            "tokens_periods": tokens_periods,
            "activity": _claude_activity(root.parent / "stats-cache.json"),
            "source": "claude_jsonl",
        }
    except Exception:  # noqa: BLE001 - leitura best-effort, nunca estoura
        logger.debug(
            "claude_metrics_for falhou para work_dir=%r", work_dir, exc_info=True
        )
        return None
