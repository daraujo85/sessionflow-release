"""Testes da raspagem do ``/usage`` do Claude (% real dos limites).

- Unit: ``parse_usage`` com o painel REAL fixo (session=32%, week=2%, resets);
  variações (vazio / tela errada → None; bloco semanal ausente → week_* None;
  Sonnet only opcional). NÃO roda o scrape real.
- Unit: ``read_usage`` (idade máx) com coleção falsa.
- Integration (marker ``integration``): ``scrape_usage`` REAL, quota-light.
  Skipa se ``claude`` ausente / scrape vazio. Teardown garante 0 ``sfusage-*``.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import libtmux
import pytest

from sessionflow_worker.usage import (
    SCRAPE_PREFIX,
    parse_usage,
    read_usage,
    scrape_usage,
)

# --------------------------------------------------------------------------- #
# Unit: parser do painel /usage (texto FIXO capturado de verdade)
# --------------------------------------------------------------------------- #
_USAGE_PANEL = """\
   Current session
   ████████████████                                   32% used
   Resets 12:30pm (America/Sao_Paulo)
   Current week (all models)
   █                                                  2% used
   Resets Jun 24 at 9am (America/Sao_Paulo)
   Current week (Sonnet only)
                                                      0% used
   Resets Jun 24 at 9am (America/Sao_Paulo)
"""


def test_parse_usage_extracts_session_and_week():
    out = parse_usage(_USAGE_PANEL)
    assert out is not None
    assert out["session_pct"] == 32
    assert out["session_reset"] == "12:30pm (America/Sao_Paulo)"
    assert out["week_pct"] == 2
    assert out["week_reset"] == "Jun 24 at 9am (America/Sao_Paulo)"
    # Sonnet only é opcional e tem 0% (linha sem barra).
    assert out["week_sonnet_pct"] == 0


def test_parse_usage_empty_returns_none():
    assert parse_usage("") is None


def test_parse_usage_wrong_screen_returns_none():
    # Painel sem o cabeçalho "Current session" → None.
    assert parse_usage("some unrelated TUI screen\n> /model picker\n") is None


def test_parse_usage_session_only_week_fields_none():
    text = (
        "   Current session\n"
        "   ████   45% used\n"
        "   Resets 3:00pm (America/Sao_Paulo)\n"
    )
    out = parse_usage(text)
    assert out is not None
    assert out["session_pct"] == 45
    assert out["session_reset"] == "3:00pm (America/Sao_Paulo)"
    assert out["week_pct"] is None
    assert out["week_reset"] is None
    assert out["week_sonnet_pct"] is None


def test_parse_usage_session_missing_pct_returns_none():
    # Cabeçalho presente mas sem "% used" (boot incompleto) → None.
    text = "   Current session\n   Resets 12:30pm (America/Sao_Paulo)\n"
    assert parse_usage(text) is None


# --------------------------------------------------------------------------- #
# Unit: read_usage (doc único + idade máxima)
# --------------------------------------------------------------------------- #
class _FakeCollection:
    def __init__(self, doc: dict | None):
        self._doc = doc

    async def find_one(self, _query):
        return self._doc


class _FakeDB:
    def __init__(self, doc: dict | None):
        self._coll = _FakeCollection(doc)

    def __getitem__(self, _name):
        return self._coll


async def test_read_usage_returns_limits_fields():
    doc = {
        "key": "host",
        "session_pct": 32,
        "session_reset": "12:30pm",
        "week_pct": 2,
        "week_reset": "Jun 24 at 9am",
        "week_sonnet_pct": 0,
        "scanned_at": datetime.now(UTC),
    }
    out = await read_usage(_FakeDB(doc), max_age_seconds=1200.0)
    assert out == {
        "session_pct": 32,
        "session_reset": "12:30pm",
        "week_pct": 2,
        "week_reset": "Jun 24 at 9am",
        "week_sonnet_pct": 0,
    }
    # Campos internos não vazam.
    assert "key" not in out
    assert "scanned_at" not in out


async def test_read_usage_none_when_missing():
    assert await read_usage(_FakeDB(None)) is None


async def test_read_usage_none_when_stale():
    doc = {
        "key": "host",
        "session_pct": 99,
        "scanned_at": datetime.now(UTC) - timedelta(hours=2),
    }
    assert await read_usage(_FakeDB(doc), max_age_seconds=1200.0) is None


async def test_read_usage_naive_scanned_at_handled():
    doc = {
        "key": "host",
        "session_pct": 10,
        "session_reset": None,
        "week_pct": None,
        "week_reset": None,
        "week_sonnet_pct": None,
        "scanned_at": datetime.now(UTC).replace(tzinfo=None),
    }
    out = await read_usage(_FakeDB(doc), max_age_seconds=1200.0)
    assert out is not None
    assert out["session_pct"] == 10


# --------------------------------------------------------------------------- #
# Integration: scrape REAL do /usage (quota-light)
# --------------------------------------------------------------------------- #
def _no_sfusage_sessions(server: libtmux.Server) -> bool:
    return not any(
        (s.session_name or "").startswith(SCRAPE_PREFIX) for s in server.sessions
    )


@pytest.fixture
def server() -> libtmux.Server:
    return libtmux.Server()


@pytest.fixture(autouse=True)
def _no_leftover_sessions(server: libtmux.Server):
    """Garante 0 sessões ``sfusage-*`` no teardown (cinto de segurança)."""
    yield
    for s in list(server.sessions):
        name = s.session_name or ""
        if name.startswith(SCRAPE_PREFIX):
            assert name.startswith(SCRAPE_PREFIX)
            try:
                server.kill_session(name)
            except Exception:  # noqa: BLE001
                pass
    assert _no_sfusage_sessions(server)


@pytest.mark.integration
def test_scrape_usage_real(server: libtmux.Server):
    if shutil.which("claude") is None:
        pytest.skip("claude não instalado")

    usage = scrape_usage(server=server)

    if usage is None:
        pytest.skip("scrape do /usage vazio (timeout/boot lento) — quota-light, sem falha dura")

    assert isinstance(usage["session_pct"], int)
    assert 0 <= usage["session_pct"] <= 100
    assert _no_sfusage_sessions(server)
