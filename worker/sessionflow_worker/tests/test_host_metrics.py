"""Unit puro (sem tmux/mongo/rabbit) — psutil e urllib mockados."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import sessionflow_worker.host_metrics as host_metrics


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_fetch_ollama_models_merges_tags_and_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "/api/tags": {
            "models": [
                {"name": "qwen3:0.6b", "size": 512 * 1024**2, "modified_at": "2026-08-01"},
                {"name": "llama3.1:8b", "size": 4 * 1024**3, "modified_at": "2026-07-01"},
            ]
        },
        "/api/ps": {"models": [{"name": "qwen3:0.6b"}]},
    }

    def fake_urlopen(req, timeout=4):  # noqa: ANN001 - assinatura espelha urllib
        for path, payload in responses.items():
            if req.full_url.endswith(path):
                return _FakeResp(payload)
        raise AssertionError(f"URL inesperada: {req.full_url}")

    monkeypatch.setattr(host_metrics.urllib.request, "urlopen", fake_urlopen)

    models = host_metrics.fetch_ollama_models()

    assert models == [
        {"name": "qwen3:0.6b", "size_gb": 0.5, "modified_at": "2026-08-01", "loaded": True},
        {"name": "llama3.1:8b", "size_gb": 4.0, "modified_at": "2026-07-01", "loaded": False},
    ]


def test_fetch_ollama_models_offline_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout=4):  # noqa: ANN001
        raise OSError("connection refused")

    monkeypatch.setattr(host_metrics.urllib.request, "urlopen", fake_urlopen)

    assert host_metrics.fetch_ollama_models() == []


def test_fetch_ollama_models_ps_failure_keeps_tags_unloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout=4):  # noqa: ANN001
        if req.full_url.endswith("/api/tags"):
            return _FakeResp({"models": [{"name": "qwen3:0.6b", "size": 0, "modified_at": None}]})
        raise OSError("ps indisponível")

    monkeypatch.setattr(host_metrics.urllib.request, "urlopen", fake_urlopen)

    models = host_metrics.fetch_ollama_models()
    assert models == [{"name": "qwen3:0.6b", "size_gb": 0.0, "modified_at": None, "loaded": False}]


def test_sample_host_metrics_reports_cpu_mem_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 42.3)
    monkeypatch.setattr(
        host_metrics.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=55.0, used=8 * 1024**3, total=16 * 1024**3),
    )
    monkeypatch.setattr(
        host_metrics.psutil,
        "disk_usage",
        lambda mount: SimpleNamespace(total=500 * 1024**3, used=250 * 1024**3, percent=50.0),
    )
    monkeypatch.setattr(
        host_metrics.psutil,
        "net_io_counters",
        lambda: SimpleNamespace(bytes_sent=0, bytes_recv=0),
    )
    # Sem leitura anterior -> taxa de rede ainda não calculável (None).
    monkeypatch.setattr(host_metrics, "_last_net", None)

    result = host_metrics.sample_host_metrics(disks=[{"mount": "/", "total_gb": 500, "used_gb": 200}])

    assert result["cpu_pct"] == 42.3
    assert result["mem_pct"] == 55.0
    assert result["mem_used_gb"] == 8.0
    assert result["mem_total_gb"] == 16.0
    assert result["disks"] == [{"mount": "/", "total_gb": 500.0, "used_gb": 250.0, "pct": 50.0}]
    assert result["net_up_kbps"] is None
    assert result["net_down_kbps"] is None


def test_sample_load_first_call_cpu_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1ª chamada do processo: psutil devolve 0.0 (sem amostra anterior) →
    # host recém-reiniciado pareceria ocioso e venceria o --host auto.
    monkeypatch.setattr(host_metrics, "_cpu_primed", False)
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 0.0)
    assert host_metrics.sample_load()["cpu_pct"] is None
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 33.0)
    assert host_metrics.sample_load()["cpu_pct"] == 33.0


def test_sample_load_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_metrics, "_cpu_primed", True)
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 12.5)
    vm = SimpleNamespace(percent=40.0, total=16 * 1024**3, available=6 * 1024**3)
    monkeypatch.setattr(host_metrics.psutil, "virtual_memory", lambda: vm)
    assert host_metrics.sample_load() == {
        "cpu_pct": 12.5, "mem_pct": 40.0, "mem_total_gb": 16.0, "mem_avail_gb": 6.0,
    }


def test_sample_load_probe_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("x")
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", boom)
    monkeypatch.setattr(host_metrics.psutil, "virtual_memory", boom)
    assert host_metrics.sample_load() == {
        "cpu_pct": None, "mem_pct": None, "mem_total_gb": None, "mem_avail_gb": None,
    }


def test_installed_agents_finds_user_bin_under_minimal_systemd_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Worker como unit systemd tem PATH mínimo (sem ~/.local/bin), mas o agent
    # roda num tmux com o PATH do usuário → o claude dali tem que contar.
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    exe = bindir / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/sbin:/usr/bin")
    assert host_metrics.installed_agents()["claude"] is True


def test_gpu_vram_finds_wsl_nvidia_smi_under_minimal_systemd_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # WSL põe o nvidia-smi em /usr/lib/wsl/lib, fora do PATH mínimo da unit
    # systemd (Duck) → sem isso a VRAM nunca era publicada.
    wsl_lib = tmp_path / "wsl-lib"
    wsl_lib.mkdir()
    exe = wsl_lib / "nvidia-smi"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(host_metrics, "_NVIDIA_SMI_EXTRA_DIRS", (str(wsl_lib),))
    monkeypatch.setenv("PATH", "/usr/sbin:/usr/bin")
    calls = []

    def fake_run(cmd, **kw):  # noqa: ANN001, ANN003
        calls.append(cmd[0])
        return SimpleNamespace(returncode=0, stdout="12288, 4401\n")

    monkeypatch.setattr(host_metrics.subprocess, "run", fake_run)
    assert host_metrics._get_gpu_vram() == {"vram_total_gb": 12.0, "vram_used_gb": 4.3}
    assert calls == [str(exe)]

def test_gpu_vram_without_nvidia_smi_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_metrics, "_NVIDIA_SMI_EXTRA_DIRS", ())
    monkeypatch.setattr(host_metrics.shutil, "which", lambda b, path=None: None)
    assert host_metrics._get_gpu_vram() == {}

def test_installed_agents_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    present = {"claude", "gemini"}
    monkeypatch.setattr(host_metrics.shutil, "which", lambda b, path=None: f"/bin/{b}" if b in present else None)
    assert host_metrics.installed_agents() == {
        "claude": True, "codex": False, "gemini": True, "opencode": False, "agy": False,
    }


def test_sample_host_metrics_disk_failure_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_metrics.psutil, "cpu_percent", lambda interval=None: 1.0)
    monkeypatch.setattr(
        host_metrics.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=1.0, used=1, total=1),
    )

    def fail_disk_usage(mount):  # noqa: ANN001
        raise OSError("unidade offline")

    monkeypatch.setattr(host_metrics.psutil, "disk_usage", fail_disk_usage)
    monkeypatch.setattr(
        host_metrics.psutil,
        "net_io_counters",
        lambda: SimpleNamespace(bytes_sent=0, bytes_recv=0),
    )

    result = host_metrics.sample_host_metrics(disks=[{"mount": "/mnt/offline"}])
    assert result["disks"] == []
