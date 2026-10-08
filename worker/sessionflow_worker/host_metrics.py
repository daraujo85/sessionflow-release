"""Métricas de host EM TEMPO REAL (CPU/RAM/disco/rede) + inventário de modelos
Ollama (tamanho em disco + carregados agora). Publicado via SSE (ver
``host_metrics_loop`` em ``runner.py``) — sem endpoint HTTP novo, sem
persistência em Mongo (é estado de agora, não histórico).

Complementa ``host_identity.hardware_info`` (estático, 1x no boot): aqui os
valores mudam a cada chamada.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.request
from typing import Any

import psutil

from sessionflow_worker.jarvis import OLLAMA_BASE

logger = logging.getLogger(__name__)

_OLLAMA_TIMEOUT = 4

# Estado do cálculo de taxa de rede (bytes/s) — psutil só dá contadores
# cumulativos; a taxa exige duas leituras + o tempo entre elas. Guardado em
# módulo (1 worker = 1 processo = 1 leitura anterior, sem precisar de classe).
_last_net: tuple[float, int, int] | None = None  # (timestamp, bytes_sent, bytes_recv)


def sample_host_metrics(disks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """CPU%, memória, disco (dos mounts já conhecidos) e taxa de rede (KB/s).

    ``disks`` (opcional): lista ``[{mount, total_gb, used_gb}]`` de
    ``host_identity.hardware_info`` — reconsultamos só os mounts já
    detectados, evitando redescobrir partições a cada 3s.

    Best-effort: qualquer probe que falhar retorna ``None`` no campo
    correspondente, nunca derruba a amostra inteira.
    """
    global _last_net

    cpu_pct = None
    mem_pct = mem_used_gb = mem_total_gb = None
    try:
        cpu_pct = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_host_metrics: cpu_percent falhou", exc_info=True)
    try:
        vm = psutil.virtual_memory()
        mem_pct = round(vm.percent, 1)
        mem_used_gb = round(vm.used / (1024**3), 1)
        mem_total_gb = round(vm.total / (1024**3), 1)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_host_metrics: virtual_memory falhou", exc_info=True)

    disk_usage: list[dict[str, Any]] = []
    for d in disks or []:
        mount = d.get("mount")
        if not mount:
            continue
        try:
            usage = psutil.disk_usage(mount)
            disk_usage.append(
                {
                    "mount": mount,
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "pct": round(usage.percent, 1),
                }
            )
        except (OSError, PermissionError):
            continue

    net_up_kbps = net_down_kbps = None
    try:
        counters = psutil.net_io_counters()
        now = time.monotonic()
        if _last_net is not None:
            prev_t, prev_sent, prev_recv = _last_net
            elapsed = now - prev_t
            if elapsed > 0:
                net_up_kbps = round(max(0, counters.bytes_sent - prev_sent) / elapsed / 1024, 1)
                net_down_kbps = round(max(0, counters.bytes_recv - prev_recv) / elapsed / 1024, 1)
        _last_net = (now, counters.bytes_sent, counters.bytes_recv)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_host_metrics: net_io_counters falhou", exc_info=True)

    return {
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "mem_used_gb": mem_used_gb,
        "mem_total_gb": mem_total_gb,
        "disks": disk_usage,
        "net_up_kbps": net_up_kbps,
        "net_down_kbps": net_down_kbps,
    }


def fetch_ollama_models() -> list[dict[str, Any]]:
    """Modelos Ollama instalados (nome, tamanho em GB, data) + quais estão
    carregados agora (``/api/ps``). Ollama offline/sem rede → lista vazia.
    """
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=_OLLAMA_TIMEOUT) as resp:
            tags = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - Ollama offline ou sem rede
        return []

    loaded_names: set[str] = set()
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=_OLLAMA_TIMEOUT) as resp:
            ps = json.loads(resp.read().decode("utf-8", errors="replace"))
        loaded_names = {m.get("name", "") for m in ps.get("models", [])}
    except Exception:  # noqa: BLE001 - /api/ps é best-effort (badge "rodando" só)
        logger.debug("fetch_ollama_models: /api/ps falhou", exc_info=True)

    models: list[dict[str, Any]] = []
    for m in tags.get("models", []):
        name = m.get("name", "")
        if not name:
            continue
        models.append(
            {
                "name": name,
                "size_gb": round(m.get("size", 0) / (1024**3), 2),
                "modified_at": m.get("modified_at"),
                "loaded": name in loaded_names,
            }
        )
    return models


# Binários das CLIs de agente que o `sf delegate --host auto` exige no host
# (espelha AgentType de agent_launcher; "agy" = Antigravity).
_AGENT_BINARIES = ("claude", "codex", "gemini", "opencode", "agy")


_cpu_primed = False  # sample_load já chamou cpu_percent 1x neste processo?


# WSL expõe o nvidia-smi do driver Windows em /usr/lib/wsl/lib — fora do PATH
# mínimo da unit systemd (Duck). Mesma classe de bug de ``installed_agents``.
_NVIDIA_SMI_EXTRA_DIRS = ("/usr/lib/wsl/lib", "/usr/local/bin")

def _get_gpu_vram() -> dict[str, Any]:
    """Retorna { vram_total_gb: X, vram_used_gb: Y } usando nvidia-smi, se existir."""
    search = os.pathsep.join([os.environ.get("PATH", ""), *_NVIDIA_SMI_EXTRA_DIRS])
    exe = shutil.which("nvidia-smi", path=search)
    if not exe:
        return {}
    try:
        res = subprocess.run(
            [exe, "--query-gpu=memory.total,memory.used", "--format=csv,nounits,noheader"],
            capture_output=True, text=True, timeout=1
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().split('\n')
            if lines:
                parts = lines[0].split(',')
                total_mb = float(parts[0].strip())
                used_mb = float(parts[1].strip())
                return {
                    "vram_total_gb": round(total_mb / 1024, 1),
                    "vram_used_gb": round(used_mb / 1024, 1),
                }
    except Exception:
        pass
    return {}


def sample_load() -> dict[str, Any]:
    """Carga atual p/ o heartbeat (`sf delegate --host auto` ranqueia por isso).

    ``cpu_percent(interval=None)`` = média desde a chamada anterior, ou seja,
    do último intervalo de heartbeat — já suavizado, sem EMA. ``mem_avail_gb``
    usa ``available`` (inclui cache liberável), não ``free``. Best-effort:
    probe que falhar vira ``None``. A 1ª chamada do processo não tem amostra
    anterior (psutil devolve 0.0) → publica ``None``, senão um worker recém-
    reiniciado pareceria ocioso e venceria o ranking.
    """
    global _cpu_primed
    load: dict[str, Any] = {
        "cpu_pct": None, "mem_pct": None, "mem_total_gb": None, "mem_avail_gb": None,
    }
    try:
        cpu = psutil.cpu_percent(interval=None)
        if _cpu_primed:
            load["cpu_pct"] = cpu
        _cpu_primed = True
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_load: cpu_percent falhou", exc_info=True)
    try:
        vm = psutil.virtual_memory()
        load["mem_pct"] = round(vm.percent, 1)
        load["mem_total_gb"] = round(vm.total / (1024**3), 1)
        load["mem_avail_gb"] = round(vm.available / (1024**3), 1)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("sample_load: virtual_memory falhou", exc_info=True)

    gpu_metrics = _get_gpu_vram()
    if gpu_metrics:
        load.update(gpu_metrics)

    return load


def installed_agents() -> dict[str, bool]:
    """Quais CLIs de agente existem neste host.

    PATH do processo + diretórios de instalação do usuário: o worker como unit
    systemd (Duck) tem o PATH MÍNIMO do systemd (sem ``~/.local/bin``, onde o
    instalador do claude põe o binário), mas o agent roda num tmux com o PATH
    do usuário — só o PATH do processo daria ``claude: False`` falso e o
    ``--host auto`` descartaria o host. Mesma classe de bug do ``cmd.exe`` em
    ``host_identity._windows_version_from_wsl``.
    """
    home = os.path.expanduser("~")
    extra = [os.path.join(home, d) for d in (".local/bin", ".npm-global/bin", "bin")]
    extra += ["/usr/local/bin", "/opt/homebrew/bin"]
    search = os.pathsep.join([os.environ.get("PATH", ""), *extra])
    return {b: shutil.which(b, path=search) is not None for b in _AGENT_BINARIES}
