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


def test_gpu_name_falls_back_to_wsl_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regressão real: nvidia-smi puro não é achado no PATH mínimo do serviço
    # systemd (só o binário absoluto em /usr/lib/wsl/lib funciona nesse
    # contexto) — sem o fallback, uma GPU real (RTX 3060) não era detectada.
    monkeypatch.setattr(host_identity_mod.platform, "system", lambda: "Linux")

    def fake_run(cmd, timeout=5.0):  # noqa: ANN001
        if cmd[0] == "/usr/lib/wsl/lib/nvidia-smi":
            return "NVIDIA GeForce RTX 3060"
        return None

    monkeypatch.setattr(host_identity_mod, "_run", fake_run)
    assert host_identity_mod._gpu_name() == "NVIDIA GeForce RTX 3060"


def test_gpu_name_none_without_any_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_identity_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(host_identity_mod, "_run", lambda *a, **kw: None)
    assert host_identity_mod._gpu_name() is None


def test_windows_total_ram_gb_parses_powershell_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regressão real: psutil dentro do WSL2 reporta a RAM da VM (limitada por
    # padrão a ~50% do total), não a física real — um host com 32GB físicos
    # aparecia como ~16GB. 34359738368 bytes = 32GB.
    monkeypatch.setattr(host_identity_mod, "_run", lambda *a, **kw: "34359738368")
    assert host_identity_mod._windows_total_ram_gb() == 32.0


def test_windows_total_ram_gb_none_without_interop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_identity_mod, "_run", lambda *a, **kw: None)
    assert host_identity_mod._windows_total_ram_gb() is None


def test_hardware_info_prefers_windows_ram_over_wsl_vm_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        host_identity_mod, "_windows_total_ram_gb", lambda: 32.0
    )
    monkeypatch.setattr(host_identity_mod, "_cpu_model", lambda: None)
    monkeypatch.setattr(host_identity_mod, "_gpu_name", lambda: None)
    monkeypatch.setattr(host_identity_mod, "_os_detail", lambda plat: {})
    monkeypatch.setattr(host_identity_mod, "_disks", lambda: [])

    class _FakeVM:
        total = 16 * 1024**3  # a VM do WSL2 só enxerga metade

    monkeypatch.setattr(
        host_identity_mod.psutil, "virtual_memory", lambda: _FakeVM()
    )
    info = host_identity_mod.hardware_info("wsl2")
    assert info["ram_total_gb"] == 32.0
