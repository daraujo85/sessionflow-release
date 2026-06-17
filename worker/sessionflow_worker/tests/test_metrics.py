"""Testes unitários de ``metrics.claude_metrics_for`` (sem tmux/Mongo).

Usa um ``projects_root`` injetável apontando para um tmpdir no formato
``<root>/<cwd-encoded>/<uuid>.jsonl`` com linhas de usage controladas.
Determinístico — sem o marker ``integration``.
"""

from __future__ import annotations

import json
from pathlib import Path

from sessionflow_worker.metrics import (
    CONTEXT_MAX_1M,
    DEFAULT_CONTEXT_MAX,
    _encode_work_dir,
    claude_metrics_for,
)


def _usage_line(
    *,
    model: str = "claude-opus-4-8",
    input_tokens: int = 0,
    cache_read: int = 0,
    cache_creation: int = 0,
    output_tokens: int = 0,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                    "output_tokens": output_tokens,
                },
            },
        }
    )


def _write_session(
    root: Path, work_dir: str, lines: list[str], name: str = "sess.jsonl"
) -> Path:
    project_dir = root / _encode_work_dir(work_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    jsonl = project_dir / name
    jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl


def test_computes_context_and_accumulated_output(tmp_path: Path) -> None:
    work_dir = "/Users/diego/Documents/projects/pvax"
    lines = [
        _usage_line(input_tokens=10, cache_read=100, cache_creation=50, output_tokens=5),
        _usage_line(input_tokens=2, cache_read=5000, cache_creation=1000, output_tokens=42),
    ]
    _write_session(tmp_path, work_dir, lines)

    m = claude_metrics_for(work_dir, projects_root=tmp_path)

    assert m is not None
    # context_used = última linha: 2 + 5000 + 1000
    assert m["context_used"] == 6002
    assert m["tokens_in"] == 6002
    # tokens_out = soma de TODAS as linhas: 5 + 42
    assert m["tokens_out"] == 47
    assert m["model"] == "Opus 4.8"
    assert m["context_max"] == DEFAULT_CONTEXT_MAX
    assert m["context_pct"] == round(6002 / DEFAULT_CONTEXT_MAX * 100)
    assert m["source"] == "claude_jsonl"


def test_picks_most_recent_jsonl(tmp_path: Path) -> None:
    work_dir = "/Users/diego/proj/x"
    old = _write_session(
        tmp_path, work_dir, [_usage_line(input_tokens=999, output_tokens=1)], name="old.jsonl"
    )
    new = _write_session(
        tmp_path, work_dir, [_usage_line(input_tokens=7, output_tokens=2)], name="new.jsonl"
    )
    # Garante que "new" é mais recente que "old".
    import os
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    m = claude_metrics_for(work_dir, projects_root=tmp_path)
    assert m is not None
    assert m["context_used"] == 7  # veio do new.jsonl


def test_missing_work_dir_returns_none(tmp_path: Path) -> None:
    m = claude_metrics_for("/no/such/dir/ever", projects_root=tmp_path)
    assert m is None


def test_no_usage_lines_returns_none(tmp_path: Path) -> None:
    work_dir = "/Users/diego/proj/empty"
    _write_session(
        tmp_path,
        work_dir,
        [json.dumps({"type": "user", "message": {"role": "user"}}), "not-json", ""],
    )
    m = claude_metrics_for(work_dir, projects_root=tmp_path)
    assert m is None


def test_1m_model_uses_million_context(tmp_path: Path) -> None:
    work_dir = "/Users/diego/proj/big"
    _write_session(
        tmp_path,
        work_dir,
        [_usage_line(model="claude-opus-4-8[1m]", input_tokens=300_000, output_tokens=3)],
    )
    m = claude_metrics_for(work_dir, projects_root=tmp_path)
    assert m is not None
    assert m["context_max"] == CONTEXT_MAX_1M
    assert m["context_pct"] == round(300_000 / CONTEXT_MAX_1M * 100)


def test_encode_expands_home_and_replaces_dots(tmp_path: Path) -> None:
    encoded = _encode_work_dir("/Users/diego/Documents/projects/pvax")
    assert encoded == "-Users-diego-Documents-projects-pvax"
