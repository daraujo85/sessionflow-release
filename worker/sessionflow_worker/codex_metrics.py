"""Métricas REAIS de sessões Codex, lidas dos rollout JSONL do Codex CLI.

O Codex CLI grava cada sessão em
``~/.codex/sessions/<ano>/<mês>/<dia>/rollout-<timestamp>-<uuid>.jsonl``
(por DATA, não por diretório de projeto como o Claude Code). A 1ª linha de
cada arquivo é sempre um evento ``session_meta`` com ``payload.cwd`` — é
assim que localizamos qual rollout pertence a qual sessão SessionFlow (que
só conhece o ``work_dir``).

Cada linha subsequente relevante:
- ``turn_context`` (top-level ``type``): ``payload.model`` — modelo em uso
  NESSE turno (pode mudar mid-sessão via ``/model``); guardamos o último visto.
- ``event_msg`` com ``payload.type == "token_count"``: ``payload.info`` traz
  ``total_token_usage`` (CUMULATIVO desde o início da sessão — cresce
  monotonicamente), ``last_token_usage`` (uso da chamada/janela ATUAL, análogo
  ao ``context_used`` do Claude) e ``model_context_window`` (tamanho real da
  janela, já vem pronto — sem precisar inferir por heurística como no Claude).

Não há tabela de preço confiável pro Codex/GPT aqui ainda — ``cost`` sai
``None`` (nunca inventamos valor; mesma regra do Claude pra modelo sem preço
mapeado). Tokens são reportados normalmente.

Este módulo é **somente leitura**. Qualquer falha degrada para ``None``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("sessionflow_worker.codex_metrics")

#: Raiz padrão das sessões do Codex CLI. Injetável em testes.
DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

#: Contexto padrão quando ``model_context_window`` não vier no evento
#: (nunca deveria faltar, mas é best-effort).
DEFAULT_CONTEXT_MAX = 200_000

#: Teto de arquivos varridos por chamada (proteção — na prática o match
#: certo está entre os poucos mais recentes, já que ordenamos por mtime desc
#: e paramos no primeiro ``cwd`` compatível).
_MAX_FILES_SCANNED = 500

#: Mesmas janelas rolantes do Claude (ver ``metrics.py``) — consistência do
#: filtro hoje/semana/mês do Top 3 da Home entre os dois agentes.
_PERIOD_WINDOWS = {
    "today": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}


def _recent_rollout_files(root: Path) -> list[Path]:
    """Todos os ``rollout-*.jsonl`` sob ``root``, mais recentemente modificado primeiro."""
    try:
        files = [p for p in root.rglob("*.jsonl") if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:_MAX_FILES_SCANNED]


def _session_meta_cwd(path: Path) -> str | None:
    """Lê só a 1ª linha (``session_meta``) e devolve o ``cwd``, ou ``None``."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return None
    try:
        obj = json.loads(first)
    except (json.JSONDecodeError, ValueError):
        return None
    if obj.get("type") != "session_meta":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) else None


def _find_rollout_for(work_dir: str, root: Path) -> Path | None:
    """Acha o rollout cujo ``cwd`` bate com ``work_dir`` (o mais recente primeiro)."""
    target = str(Path(work_dir).expanduser().resolve(strict=False))
    for path in _recent_rollout_files(root):
        cwd = _session_meta_cwd(path)
        if cwd is None:
            continue
        try:
            resolved = str(Path(cwd).expanduser().resolve(strict=False))
        except OSError:
            resolved = cwd
        if resolved == target:
            return path
    return None


def _iter_lines(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def codex_metrics_for(
    work_dir: str,
    sessions_root: str | os.PathLike[str] | None = None,
) -> dict | None:
    """Métricas REAIS da sessão Codex cujo ``cwd`` é ``work_dir``.

    Mesmo formato de saída do ``claude_metrics_for`` (drop-in — frontend/API
    não precisam saber a diferença): ``model``, ``cost`` (sempre ``None`` por
    ora), ``context_used``, ``context_max``, ``context_pct``, ``tokens_in``,
    ``tokens_out``, ``tokens_periods``, ``source``.

    ``tokens_out`` é o total CUMULATIVO de output da sessão inteira (do
    último evento ``token_count``); ``tokens_in``/``context_used`` é o
    ``last_token_usage`` (uso da janela ATUAL, não cumulativo — mesma
    semântica do ``context_used`` do Claude). Os períodos (hoje/semana/mês)
    são a soma dos DELTAS entre eventos consecutivos (o campo já vem
    cumulativo; delta = quanto essa chamada específica consumiu).

    Retorna ``None`` se não achar o rollout ou não houver nenhum evento de
    uso de token nele.
    """
    try:
        root = Path(sessions_root) if sessions_root is not None else DEFAULT_SESSIONS_ROOT
        if not root.is_dir():
            return None

        rollout = _find_rollout_for(work_dir, root)
        if rollout is None:
            return None

        last_model: str | None = None
        last_total: dict | None = None
        last_window_usage: dict | None = None
        context_max: int | None = None
        prev_in = 0
        prev_out = 0
        saw_usage = False
        now = datetime.now(timezone.utc)
        period_usage: dict[str, dict[str, int]] = {
            key: {"input": 0, "output": 0} for key in _PERIOD_WINDOWS
        }

        for obj in _iter_lines(rollout):
            if obj.get("type") == "turn_context":
                payload = obj.get("payload")
                if isinstance(payload, dict) and payload.get("model"):
                    last_model = payload["model"]
                continue

            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total = info.get("total_token_usage")
            if not isinstance(total, dict):
                continue

            saw_usage = True
            cur_in = int(total.get("input_tokens", 0) or 0)
            cur_out = int(total.get("output_tokens", 0) or 0)
            # Delta desde o evento anterior — o campo é cumulativo; a diferença
            # é o que ESSA chamada consumiu (usado só p/ os buckets de período).
            delta_in = max(0, cur_in - prev_in)
            delta_out = max(0, cur_out - prev_out)
            prev_in, prev_out = cur_in, cur_out

            ts: datetime | None = None
            raw_ts = obj.get("timestamp")
            if isinstance(raw_ts, str):
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    ts = None
            if ts is not None:
                for period, window in _PERIOD_WINDOWS.items():
                    if now - ts <= window:
                        bucket = period_usage[period]
                        bucket["input"] += delta_in
                        bucket["output"] += delta_out

            last_total = total
            window_usage = info.get("last_token_usage")
            if isinstance(window_usage, dict):
                last_window_usage = window_usage
            window_max = info.get("model_context_window")
            if isinstance(window_max, (int, float)) and window_max:
                context_max = int(window_max)

        if not saw_usage or last_total is None:
            return None

        tokens_out_total = int(last_total.get("output_tokens", 0) or 0)
        context_used = int((last_window_usage or {}).get("input_tokens", 0) or 0)
        if context_max is None:
            context_max = DEFAULT_CONTEXT_MAX
        context_pct = (
            min(100, round(context_used / context_max * 100)) if context_max else 0
        )

        tokens_periods: dict[str, dict] = {
            period: {
                "tokens_in": bucket["input"],
                "tokens_out": bucket["output"],
                # Sem tabela de preço confiável pro Codex/GPT ainda — nunca
                # inventamos valor (mesma regra do Claude p/ modelo sem preço).
                "cost": None,
            }
            for period, bucket in period_usage.items()
        }

        return {
            "model": last_model,
            "cost": None,
            "context_used": context_used,
            "context_max": context_max,
            "context_pct": context_pct,
            "tokens_in": context_used,
            "tokens_out": tokens_out_total,
            "tokens_periods": tokens_periods,
            "source": "codex_jsonl",
        }
    except Exception:  # noqa: BLE001 - leitura best-effort, nunca estoura
        logger.debug(
            "codex_metrics_for falhou para work_dir=%r", work_dir, exc_info=True
        )
        return None
