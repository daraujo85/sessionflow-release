"""Testes de ``host_identity.capabilities_for`` (puro, sem I/O)."""

from __future__ import annotations

import pytest

from sessionflow_worker.host_identity import capabilities_for


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SESSIONFLOW_JARVIS_TTS", "SESSIONFLOW_HOST_TTS", "SESSIONFLOW_HOST_TRANSCRIPTION"):
        monkeypatch.delenv(key, raising=False)


def test_darwin_has_all_capabilities() -> None:
    caps = capabilities_for("darwin")
    assert caps == {
        "platform": "darwin",
        "tts": True,
        "transcription": True,
        "open_terminal": True,
    }


def test_wsl2_has_nothing_by_default() -> None:
    caps = capabilities_for("wsl2")
    assert caps["tts"] is False
    assert caps["transcription"] is False
    assert caps["open_terminal"] is False  # "abrir no Mac" é sempre Mac-only


def test_jarvis_tts_api_enables_tts_on_any_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_JARVIS_TTS", "api")
    caps = capabilities_for("wsl2")
    assert caps["tts"] is True
    assert caps["transcription"] is False  # api do TTS não afeta transcrição
    assert caps["open_terminal"] is False


def test_jarvis_tts_piper_enables_tts_on_any_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_JARVIS_TTS", "piper")
    caps = capabilities_for("wsl2")
    assert caps["tts"] is True
    assert caps["transcription"] is False
    assert caps["open_terminal"] is False


def test_host_tts_declares_local_xtts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_HOST_TTS", "1")
    caps = capabilities_for("wsl2")
    assert caps["tts"] is True
    assert caps["transcription"] is False


def test_host_transcription_declares_local_cuda_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSIONFLOW_HOST_TRANSCRIPTION", "1")
    caps = capabilities_for("wsl2")
    assert caps["tts"] is False
    assert caps["transcription"] is True


def test_open_terminal_never_true_off_darwin_even_with_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SESSIONFLOW_HOST_TTS", "1")
    monkeypatch.setenv("SESSIONFLOW_HOST_TRANSCRIPTION", "1")
    monkeypatch.setenv("SESSIONFLOW_JARVIS_TTS", "api")
    caps = capabilities_for("wsl2")
    assert caps["open_terminal"] is False
