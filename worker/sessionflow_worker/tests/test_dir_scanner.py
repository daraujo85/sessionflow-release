"""Testes da varredura/filtro de diretórios (TMUX-08, parcial)."""

from __future__ import annotations

import os
from pathlib import Path

from sessionflow_worker.dir_scanner import (
    DEFAULT_ROOTS,
    filter_dirs,
    scan_roots,
    to_suggestion,
)


def _make_tree(base: Path) -> Path:
    """Monta uma árvore de teste sob ``base`` e devolve a raiz ``dev``."""
    root = base / "dev"
    (root / "alpha" / "src" / "deep").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / ".git").mkdir()  # oculto -> ignorado
    (root / "node_modules" / "pkg").mkdir(parents=True)  # blocklist
    (root / "readme.txt").write_text("x")  # arquivo -> ignorado
    return root


def test_scan_ignores_hidden_and_blocklist(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    names = {p.name for p in scan_roots([root], max_depth=3)}
    assert ".git" not in names
    assert "node_modules" not in names
    assert "pkg" not in names  # filho de node_modules não é varrido
    assert "readme.txt" not in names  # arquivos ignorados
    assert {"alpha", "beta", "src"} <= names


def test_scan_respects_max_depth(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    names = {p.name for p in scan_roots([root], max_depth=1)}
    # depth=1 -> só filhos diretos da raiz
    assert "alpha" in names
    assert "src" not in names
    assert "deep" not in names


def test_scan_skips_missing_roots(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert scan_roots([missing]) == []


def test_filter_by_term_case_insensitive(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    dirs = scan_roots([root], max_depth=3)
    result = filter_dirs(dirs, query="ALPHA")
    assert all("alpha" in str(p).lower() for p in result)
    assert any(p.name == "alpha" for p in result)
    assert not any(p.name == "beta" for p in result)


def test_filter_empty_query_returns_limited(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    dirs = scan_roots([root], max_depth=3)
    result = filter_dirs(dirs, query="", limit=2)
    assert len(result) == 2


def test_filter_empty_query_orders_by_mtime_desc(tmp_path: Path) -> None:
    root = tmp_path / "dev"
    older = root / "older"
    newer = root / "newer"
    older.mkdir(parents=True)
    newer.mkdir()
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    result = filter_dirs(scan_roots([root]), query="")
    assert result[0].name == "newer"


def test_to_suggestion_collapses_home() -> None:
    path = Path.home() / "dev" / "myproj"
    suggestion = to_suggestion(path, "test-host")
    assert suggestion["name"] == "myproj"
    assert suggestion["path"] == "~/dev/myproj"
    assert suggestion["parent"] == "~/dev"
    assert suggestion["root"] == "~/dev"
    assert suggestion["host_id"] == "test-host"


def test_default_roots_are_under_home() -> None:
    home = Path.home()
    assert DEFAULT_ROOTS == [home / "dev", home / "work"]
