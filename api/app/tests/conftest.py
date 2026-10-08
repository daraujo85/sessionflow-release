"""Shared pytest configuration and fixtures for app tests."""

from __future__ import annotations

# Re-export fixtures from test_sessions_create so they're available to all tests
from app.tests.test_sessions_create import (
    _TEST_HOST_ID,
    _client,
    drain_commands,
    settings,
)

__all__ = ["_TEST_HOST_ID", "_client", "drain_commands", "settings"]
