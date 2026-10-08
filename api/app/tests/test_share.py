"""Unit tests for app.share — funções puras, sem banco/rede."""

from __future__ import annotations

from app.share import path_allows_share


def test_path_allows_share_allows_session_subpaths():
    assert path_allows_share("/sessions/abc123", "abc123")
    assert path_allows_share("/sessions/abc123/input", "abc123")
    assert path_allows_share("/sessions/abc123/clone", "abc123")


def test_path_allows_share_blocks_other_session():
    assert not path_allows_share("/sessions/other", "abc123")


def test_path_allows_share_blocks_share_management_subpath():
    assert not path_allows_share("/sessions/abc123/share", "abc123")


def test_path_allows_share_blocks_demands_subpath():
    assert not path_allows_share("/sessions/abc123/demands", "abc123")
    assert not path_allows_share("/sessions/abc123/demands/", "abc123")
