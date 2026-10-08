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


def test_session_id_picks_exact_file_when_dir_is_shared(tmp_path: Path) -> None:
    """Reproduz o bug real: 2 sessões tmux no MESMO work_dir.

    Sem ``session_id``, ambas cairiam no heurístico "mais recente na pasta"
    e leriam o MESMO arquivo (o de A, por ser o mais novo) — inflando o
    custo total ao contar a mesma conversa duas vezes sob nomes diferentes.
    Com ``session_id``, cada uma acha exatamente a sua.
    """
    work_dir = "/Users/diego/Documents/projects/shared-repo"
    sid_a = "aaaaaaaa-0000-0000-0000-000000000000"
    sid_b = "bbbbbbbb-0000-0000-0000-000000000000"
    path_a = _write_session(
        tmp_path,
        work_dir,
        [_usage_line(input_tokens=100, output_tokens=10)],
        name=f"{sid_a}.jsonl",
    )
    path_b = _write_session(
        tmp_path,
        work_dir,
        [_usage_line(input_tokens=999, output_tokens=1)],
        name=f"{sid_b}.jsonl",
    )
    import os

    os.utime(path_a, (1_000_000, 1_000_000))
    os.utime(path_b, (2_000_000, 2_000_000))  # B é a "mais recente na pasta"

    m_a = claude_metrics_for(work_dir, projects_root=tmp_path, session_id=sid_a)
    m_b = claude_metrics_for(work_dir, projects_root=tmp_path, session_id=sid_b)

    assert m_a is not None and m_b is not None
    assert m_a["context_used"] == 100  # A não "rouba" a métrica de B
    assert m_b["context_used"] == 999


def test_session_id_missing_falls_back_to_latest_heuristic(tmp_path: Path) -> None:
    """Doc legado sem ``claude_session_id`` ainda funciona (best-effort antigo)."""
    work_dir = "/Users/diego/Documents/projects/legacy"
    _write_session(
        tmp_path, work_dir, [_usage_line(input_tokens=7, output_tokens=2)], name="old.jsonl"
    )
    m = claude_metrics_for(work_dir, projects_root=tmp_path, session_id=None)
    assert m is not None
    assert m["context_used"] == 7


def test_session_id_not_found_falls_back_to_latest_heuristic(tmp_path: Path) -> None:
    """UUID que ainda não tem JSONL (turno zero) cai no heurístico, não em None."""
    work_dir = "/Users/diego/Documents/projects/fresh"
    _write_session(
        tmp_path, work_dir, [_usage_line(input_tokens=3, output_tokens=1)], name="real.jsonl"
    )
    m = claude_metrics_for(
        work_dir, projects_root=tmp_path, session_id="never-written-yet"
    )
    assert m is not None
    assert m["context_used"] == 3
