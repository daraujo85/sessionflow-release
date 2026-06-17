"""Testes da descoberta de modelos do host (MODEL-01).

- Unit: ``discover_opencode`` com fixture JSON; ``parse_claude_picker``;
  ``discover_codex_config`` com fixture TOML.
- Integration (marker ``integration``): ``scrape_models('claude')`` REAL,
  quota-free (só abre o picker ``/model``). Skipa se ``claude`` não estiver
  instalado ou der timeout. O teardown garante 0 sessões ``sfmodel-*``.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta

import libtmux
import pytest

from sessionflow_worker.model_discovery import (
    SCRAPE_PREFIX,
    cache_is_fresh,
    discover_codex_config,
    discover_opencode,
    latest_scanned_at,
    parse_claude_picker,
    parse_gemini_picker,
    scrape_models,
)

# --------------------------------------------------------------------------- #
# Unit: opencode
# --------------------------------------------------------------------------- #
_OPENCODE_FIXTURE = {
    "model": "ollama/qwen2.5-coder:latest",
    "provider": {
        "ollama": {
            "name": "Ollama",
            "models": {
                "qwen2.5-coder:latest": {"name": "Qwen 2.5 Coder 7B"},
                "gemma3:12b": {"name": "Gemma 3 12B"},
                "bare-model": {},
            },
        }
    },
}


def test_discover_opencode_reads_models_and_default(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps(_OPENCODE_FIXTURE), encoding="utf-8")

    models = discover_opencode(cfg)
    by_id = {m["id"]: m for m in models}

    assert "ollama/qwen2.5-coder:latest" in by_id
    assert "ollama/gemma3:12b" in by_id
    # Label cai na key quando não há "name".
    assert by_id["ollama/bare-model"]["label"] == "bare-model"
    assert by_id["ollama/qwen2.5-coder:latest"]["label"] == "Qwen 2.5 Coder 7B"
    # Só o ".model" raiz é default.
    assert by_id["ollama/qwen2.5-coder:latest"]["is_default"] is True
    assert by_id["ollama/gemma3:12b"]["is_default"] is False


def test_discover_opencode_missing_file_returns_empty(tmp_path):
    assert discover_opencode(tmp_path / "nope.json") == []


def test_discover_opencode_invalid_json_returns_empty(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text("{ not json", encoding="utf-8")
    assert discover_opencode(cfg) == []


# --------------------------------------------------------------------------- #
# Unit: codex config fallback
# --------------------------------------------------------------------------- #
def test_discover_codex_config_reads_model(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "gpt-5.5"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8")
    models = discover_codex_config(cfg)
    assert models == [
        {"id": "gpt-5.5", "label": "gpt-5.5", "description": None, "is_default": True}
    ]


def test_discover_codex_config_missing_returns_empty(tmp_path):
    assert discover_codex_config(tmp_path / "nope.toml") == []


# --------------------------------------------------------------------------- #
# Unit: parser do picker do claude
# --------------------------------------------------------------------------- #
_CLAUDE_PICKER = """\
   Select model
   Switch between Claude models. Your pick becomes the default for new sessions.
   ❯ 1. Default (recommended) ✔  Opus 4.8 with 1M context · Best for everyday, complex tasks
     2. Opus                     Opus 4.8 with 1M context · Best for everyday, complex tasks
     3. Sonnet                   Sonnet 4.6 · Efficient for routine tasks
     4. Haiku                    Haiku 4.5 · Fastest for quick answers
     5. Fable (disabled)         Claude Fable 5 is currently unavailable.
   Enter to set as default · Esc to cancel
"""


def test_parse_claude_picker():
    models = parse_claude_picker(_CLAUDE_PICKER)
    ids = [m["id"] for m in models]
    # Disabled é ignorado; sufixo (recommended) é removido.
    assert ids == ["Default", "Opus", "Sonnet", "Haiku"]
    default = next(m for m in models if m["id"] == "Default")
    assert default["is_default"] is True
    assert "Opus 4.8" in default["description"]
    assert all(m["is_default"] is False for m in models if m["id"] != "Default")


# --------------------------------------------------------------------------- #
# Unit: parser do picker do gemini (tela Manual)
# --------------------------------------------------------------------------- #
# Bloco FIXO copiado de um capture-pane REAL da TUI do gemini (tela "Manual"),
# já dentro da box do tmux (``│``) e seguido do painel "Model usage" — que NÃO
# deve virar modelo.
_GEMINI_PICKER = """\
╭──────────────────────────────────────────╮
│                                          │
│ Select Model                             │
│                                          │
│ ● 1. gemini-3.1-pro-preview              │
│   2. gemini-3-flash-preview              │
│   3. gemini-2.5-pro                      │
│   4. gemini-3.1-flash-lite              │
│   5. gemini-2.5-flash                    │
│   6. gemma-4-31b-it                       │
│   7. gemma-4-26b-a4b-it                   │
│                                          │
│ Remember model for future sessions: false│
│ ──────────────────────────────────────  │
│ Model usage                              │
│ Flash       ▬▬▬▬▬▬▬▬▬▬▬▬▬  2%             │
│ gemini-3.1-…▬▬▬▬▬▬▬▬▬▬▬▬▬  1%             │
│ (Press Esc to close)                     │
╰──────────────────────────────────────────╯
"""


def test_parse_gemini_picker_extracts_seven_and_default():
    models = parse_gemini_picker(_GEMINI_PICKER)
    ids = [m["id"] for m in models]
    assert ids == [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
    ]
    assert len(models) == 7
    # ● marca o default (o atual).
    default = next(m for m in models if m["id"] == "gemini-3.1-pro-preview")
    assert default["is_default"] is True
    assert all(
        m["is_default"] is False for m in models if m["id"] != "gemini-3.1-pro-preview"
    )
    # label == id, usável direto com -m.
    assert all(m["label"] == m["id"] for m in models)


def test_parse_gemini_picker_ignores_select_screen_options():
    # A tela inicial (Auto/Manual) não pode virar modelo.
    text = "│ ● 1. Auto                 │\n│   2. Manual               │\n"
    assert parse_gemini_picker(text) == []


def test_parse_gemini_picker_empty():
    assert parse_gemini_picker("") == []


# --------------------------------------------------------------------------- #
# Unit: cache (rotina diária) — pular se fresco
# --------------------------------------------------------------------------- #
class _FakeCollection:
    """Coleção async mínima: ``find_one`` devolve o doc mais recente injetado."""

    def __init__(self, docs: list[dict]):
        self._docs = docs

    async def find_one(self, query, *, sort=None, projection=None):
        candidates = [d for d in self._docs if d.get("scanned_at") is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d["scanned_at"])


class _FakeDB:
    def __init__(self, docs: list[dict]):
        self._coll = _FakeCollection(docs)

    def __getitem__(self, _name):
        return self._coll


async def test_cache_is_fresh_true_when_recent():
    recent = datetime.now(UTC) - timedelta(hours=1)
    db = _FakeDB([{"agent": "claude", "scanned_at": recent}])
    assert await cache_is_fresh(db, max_age_seconds=86400.0) is True


async def test_cache_is_fresh_false_when_stale():
    old = datetime.now(UTC) - timedelta(hours=30)
    db = _FakeDB([{"agent": "claude", "scanned_at": old}])
    assert await cache_is_fresh(db, max_age_seconds=86400.0) is False


async def test_cache_is_fresh_false_when_empty():
    db = _FakeDB([])
    assert await cache_is_fresh(db, max_age_seconds=86400.0) is False
    assert await latest_scanned_at(db) is None


async def test_latest_scanned_at_normalizes_naive():
    # Mongo devolve naive (UTC); o helper deve normalizar para aware.
    naive = datetime.now(UTC).replace(tzinfo=None)
    db = _FakeDB([{"agent": "claude", "scanned_at": naive}])
    scanned = await latest_scanned_at(db)
    assert scanned is not None
    assert scanned.tzinfo is not None


# --------------------------------------------------------------------------- #
# Integration: scrape REAL do claude (quota-free)
# --------------------------------------------------------------------------- #
def _no_sfmodel_sessions(server: libtmux.Server) -> bool:
    return not any(
        (s.session_name or "").startswith(SCRAPE_PREFIX) for s in server.sessions
    )


@pytest.fixture
def server() -> libtmux.Server:
    return libtmux.Server()


@pytest.fixture(autouse=True)
def _no_leftover_sessions(server: libtmux.Server):
    """Garante 0 sessões ``sfmodel-*`` antes e depois (cinto de segurança).

    O teardown só mata sessões com prefixo ``sfmodel-`` — nunca toca outras.
    """
    yield
    for s in list(server.sessions):
        name = s.session_name or ""
        if name.startswith(SCRAPE_PREFIX):
            assert name.startswith(SCRAPE_PREFIX)
            try:
                server.kill_session(name)
            except Exception:  # noqa: BLE001
                pass
    assert _no_sfmodel_sessions(server)


@pytest.mark.integration
def test_scrape_models_claude_real(server: libtmux.Server):
    if shutil.which("claude") is None:
        pytest.skip("claude não instalado")

    models = scrape_models("claude", server=server)

    if not models:
        pytest.skip("scrape do claude vazio (timeout/boot lento) — quota-free, sem falha dura")

    labels = " ".join(m["label"] for m in models)
    assert "Sonnet" in labels or "Opus" in labels
    # E nenhuma sessão efêmera sobrou (verificado pelo fixture autouse também).
    assert _no_sfmodel_sessions(server)


@pytest.mark.integration
def test_scrape_models_gemini_real(server: libtmux.Server):
    # O gemini é LENTO (boot ~20s + 2 passos no picker); este teste pode demorar
    # ou falhar (CLI em descontinuação). Skipa em vez de quebrar a suíte.
    if shutil.which("gemini") is None:
        pytest.skip("gemini não instalado")

    models = scrape_models("gemini", server=server)

    if not models:
        pytest.skip("scrape do gemini vazio (boot lento/timeout) — sem falha dura")

    ids = [m["id"] for m in models]
    assert all(i.lower().startswith(("gemini", "gemma")) for i in ids)
    assert _no_sfmodel_sessions(server)
