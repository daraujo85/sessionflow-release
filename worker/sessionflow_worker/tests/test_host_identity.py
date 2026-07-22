"""Testes de ``host_identity.capabilities_for`` (puro, sem I/O)."""

from __future__ import annotations

import pytest

import sessionflow_worker.host_identity as host_identity_mod
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


def test_windows_version_from_wsl_parses_localized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regressão real: Windows em PT-BR responde "[versão 10.0.22621.4317]"
    # (não "[Version ...]" em inglês) — o regex antigo nunca batia nesse caso.
    monkeypatch.setattr(
        host_identity_mod,
        "_run",
        lambda *a, **kw: "Microsoft Windows [vers\xe3o 10.0.22621.4317]",
    )
    assert host_identity_mod._windows_version_from_wsl() == "Windows (build 10.0.22621.4317)"


def test_windows_version_from_wsl_none_without_interop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_identity_mod, "_run", lambda *a, **kw: None)
    assert host_identity_mod._windows_version_from_wsl() is None


def test_disks_skips_wsl_internal_mounts(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Part:
        def __init__(self, mountpoint: str, fstype: str = "ext4") -> None:
            self.mountpoint = mountpoint
            self.fstype = fstype
            self.device = mountpoint

    class _Usage:
        def __init__(self, total: int, used: int) -> None:
            self.total = total
            self.used = used

    parts = [
        _Part("/mnt/wsl/docker-desktop/docker-desktop-user-distro"),
        _Part("/mnt/wsl/docker-desktop/cli-tools"),
        _Part("/"),
    ]
    usages = {
        "/mnt/wsl/docker-desktop/docker-desktop-user-distro": _Usage(100 * 1024**3, 1024**3),
        "/mnt/wsl/docker-desktop/cli-tools": _Usage(200 * 1024**3, 1024**3),
        "/": _Usage(1000 * 1024**3, 30 * 1024**3),
    }
    monkeypatch.setattr(
        host_identity_mod.psutil, "disk_partitions", lambda all=False: parts  # noqa: A002
    )
    monkeypatch.setattr(host_identity_mod.psutil, "disk_usage", lambda mp: usages[mp])
    disks = host_identity_mod._disks()
    assert [d["mount"] for d in disks] == ["/"]
