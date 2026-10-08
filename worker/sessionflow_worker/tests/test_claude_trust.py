"""Testes do pré-trust do claude (diálogo 'trust this folder?' em worktree novo)."""

import json
from pathlib import Path

import pytest

from sessionflow_worker.command_consumer import _ensure_claude_trust


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _read(home: Path) -> dict:
    return json.loads((home / ".claude.json").read_text(encoding="utf-8"))


def test_marks_new_dir_trusted_preserving_other_keys(home: Path) -> None:
    (home / ".claude.json").write_text(
        json.dumps({"numStartups": 3, "projects": {"/x": {"allowedTools": ["a"]}}}),
        encoding="utf-8",
    )
    work = home / "repo-clones" / "w1"
    work.mkdir(parents=True)

    _ensure_claude_trust(str(work))

    data = _read(home)
    assert data["numStartups"] == 3
    assert data["projects"]["/x"] == {"allowedTools": ["a"]}
    assert data["projects"][str(work)]["hasTrustDialogAccepted"] is True


def test_keeps_existing_project_fields(home: Path) -> None:
    work = home / "w2"
    work.mkdir()
    (home / ".claude.json").write_text(
        json.dumps({"projects": {str(work): {"lastCost": 1.5}}}), encoding="utf-8"
    )

    _ensure_claude_trust(str(work))

    proj = _read(home)["projects"][str(work)]
    assert proj == {"lastCost": 1.5, "hasTrustDialogAccepted": True}


def test_creates_file_when_missing(home: Path) -> None:
    work = home / "w3"
    work.mkdir()

    _ensure_claude_trust(str(work))

    assert _read(home)["projects"][str(work)]["hasTrustDialogAccepted"] is True


def test_already_trusted_does_not_rewrite(home: Path) -> None:
    work = home / "w4"
    work.mkdir()
    cfg = home / ".claude.json"
    raw = json.dumps({"projects": {str(work): {"hasTrustDialogAccepted": True}}})
    cfg.write_text(raw, encoding="utf-8")
    before = cfg.stat().st_mtime_ns

    _ensure_claude_trust(str(work))

    assert cfg.read_text(encoding="utf-8") == raw
    assert cfg.stat().st_mtime_ns == before


def test_corrupt_config_is_left_untouched(home: Path) -> None:
    cfg = home / ".claude.json"
    cfg.write_text("{not json", encoding="utf-8")
    work = home / "w5"
    work.mkdir()

    _ensure_claude_trust(str(work))  # best-effort: não levanta

    assert cfg.read_text(encoding="utf-8") == "{not json"
