"""Testes unitários de ``codex_metrics.codex_metrics_for`` (sem tmux/Mongo).

Usa um ``sessions_root`` injetável apontando pra um tmpdir no formato
``<root>/<data qualquer>/rollout-<nome>.jsonl`` com eventos controlados.
Determinístico — sem o marker ``integration``.
"""

from __future__ import annotations

import json
from pathlib import Path

from sessionflow_worker.codex_metrics import DEFAULT_CONTEXT_MAX, codex_metrics_for


def _session_meta(cwd: str) -> str:
    return json.dumps({"type": "session_meta", "payload": {"cwd": cwd}})


def _turn_context(model: str) -> str:
    return json.dumps({"type": "turn_context", "payload": {"model": model}})


def _token_count(
    *,
    timestamp: str = "2026-07-15T12:00:00.000Z",
    total_input: int,
    total_output: int,
    last_input: int,
    last_output: int,
    context_window: int = 258_400,
) -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                    },
                    "last_token_usage": {
                        "input_tokens": last_input,
                        "output_tokens": last_output,
                    },
                    "model_context_window": context_window,
                },
            },
        }
    )


def _write_rollout(root: Path, lines: list[str], name: str = "rollout-x.jsonl") -> Path:
    day_dir = root / "2026" / "07" / "15"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_computes_context_and_cumulative_output(tmp_path: Path) -> None:
    work_dir = "/Users/diego/Documents/projects/pvax"
    lines = [
        _session_meta(work_dir),
        _turn_context("gpt-5.5"),
        _token_count(total_input=1000, total_output=50, last_input=1000, last_output=50),
        _token_count(total_input=1800, total_output=90, last_input=800, last_output=40),
    ]
    _write_rollout(tmp_path, lines)

    m = codex_metrics_for(work_dir, sessions_root=tmp_path)

    assert m is not None
    # context_used = last_token_usage da ÚLTIMA linha de token_count.
    assert m["context_used"] == 800
    assert m["tokens_in"] == 800
    # tokens_out = total_token_usage.output_tokens CUMULATIVO (última linha).
    assert m["tokens_out"] == 90
    assert m["model"] == "gpt-5.5"
    assert m["context_max"] == 258_400
    assert m["context_pct"] == round(800 / 258_400 * 100)
    assert m["source"] == "codex_jsonl"
    assert m["cost"] is None  # sem tabela de preço pro codex ainda


def test_period_buckets_use_deltas_not_cumulative(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    work_dir = "/Users/diego/Documents/projects/deltatest"
    now = datetime.now(UTC)
    old_ts = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    recent_ts = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    lines = [
        _session_meta(work_dir),
        _turn_context("gpt-5.5"),
        # Fora da janela "month" (40 dias atrás): não deve entrar nos buckets.
        _token_count(
            timestamp=old_ts, total_input=100_000, total_output=5_000,
            last_input=100_000, last_output=5_000,
        ),
        # Dentro da janela: delta = 500 in / 20 out.
        _token_count(
            timestamp=recent_ts, total_input=100_500, total_output=5_020,
            last_input=500, last_output=20,
        ),
    ]
    _write_rollout(tmp_path, lines, name="rollout-delta.jsonl")

    m = codex_metrics_for(work_dir, sessions_root=tmp_path)
    assert m is not None
    assert m["tokens_periods"]["month"]["tokens_in"] == 500
    assert m["tokens_periods"]["month"]["tokens_out"] == 20
    assert m["tokens_periods"]["today"]["tokens_in"] == 500  # <24h também


def test_matches_by_cwd_across_multiple_rollouts(tmp_path: Path) -> None:
    work_dir_a = "/Users/diego/Documents/projects/a"
    work_dir_b = "/Users/diego/Documents/projects/b"
    _write_rollout(
        tmp_path,
        [
            _session_meta(work_dir_a),
            _turn_context("gpt-5.5"),
            _token_count(total_input=11, total_output=1, last_input=11, last_output=1),
        ],
        name="rollout-a.jsonl",
    )
    _write_rollout(
        tmp_path,
        [
            _session_meta(work_dir_b),
            _turn_context("gpt-5.5"),
            _token_count(total_input=22, total_output=2, last_input=22, last_output=2),
        ],
        name="rollout-b.jsonl",
    )

    m = codex_metrics_for(work_dir_b, sessions_root=tmp_path)
    assert m is not None
    assert m["context_used"] == 22


def test_missing_work_dir_returns_none(tmp_path: Path) -> None:
    m = codex_metrics_for("/no/such/dir/ever", sessions_root=tmp_path)
    assert m is None


def test_no_token_count_events_returns_none(tmp_path: Path) -> None:
    work_dir = "/Users/diego/Documents/projects/empty"
    _write_rollout(tmp_path, [_session_meta(work_dir), _turn_context("gpt-5.5")])
    m = codex_metrics_for(work_dir, sessions_root=tmp_path)
    assert m is None


def test_missing_context_window_falls_back_to_default(tmp_path: Path) -> None:
    work_dir = "/Users/diego/Documents/projects/nowindow"
    line = json.dumps(
        {
            "timestamp": "2026-07-15T12:00:00.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 5, "output_tokens": 1},
                    "last_token_usage": {"input_tokens": 5, "output_tokens": 1},
                },
            },
        }
    )
    _write_rollout(tmp_path, [_session_meta(work_dir), line])
    m = codex_metrics_for(work_dir, sessions_root=tmp_path)
    assert m is not None
    assert m["context_max"] == DEFAULT_CONTEXT_MAX
