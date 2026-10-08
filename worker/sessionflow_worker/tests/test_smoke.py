"""Smoke test ensuring the package imports and is versioned."""

from sessionflow_worker import __version__


def test_version_is_set() -> None:
    assert __version__
