"""Identidade do HOST onde este worker roda (multi-host, AD-011).

Cada worker gera/lê um ``host_id`` estável (persistido em disco) na primeira
subida — não usamos o hostname puro porque ele pode colidir (duas máquinas
com o mesmo nome) ou mudar (reinstalação do SO). Junto do ``host_id``, este
módulo detecta a PLATAFORMA e deriva quais features fazem sentido anunciar
pro frontend (capabilities) — evita hardcode de "é sempre Mac" espalhado
pelo código.

Ver ``docs/multi-host-plan.md`` no repo pro desenho completo.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)

#: Arquivo onde o host_id é persistido (1 por máquina, sobrevive a restarts).
HOST_ID_PATH = Path.home() / ".claude" / ".sessionflow-host-id"


def get_host_id(path: Path = HOST_ID_PATH) -> str:
    """Lê o ``host_id`` persistido, ou gera um novo (UUID4) na 1ª chamada.

    Idempotente: chamadas subsequentes (mesmo processo ou reboot) devolvem
    sempre o mesmo valor, desde que o arquivo não seja apagado.
    """
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    new_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_id)
    return new_id


def detect_platform() -> str:
    """Detecta a plataforma: ``darwin`` | ``wsl2`` | ``linux`` | ``windows``.

    Distingue WSL2 de Linux "puro" lendo ``/proc/version`` (contém
    "microsoft" dentro do WSL2) — importa pra decidir capabilities (ex.:
    Docker Desktop via integração é comum no WSL2, mas isso não afeta as
    capabilities do worker em si).
    """
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "linux":
        try:
            if "microsoft" in Path("/proc/version").read_text().lower():
                return "wsl2"
        except OSError:
            pass
        return "linux"
    return "windows"


def capabilities_for(plat: str) -> dict:
    """Deriva as CAPABILITIES anunciadas pro frontend, a partir da plataforma.

    "abrir no Mac" é macOS-only de verdade (osascript+Terminal.app). TTS e
    transcrição, porém, não são tecnicamente presas ao Mac — são presas ao
    que está rodando LOCALMENTE naquele host:

    - ``tts``: ``True`` no Mac (servidor XTTS local de sempre), ou quando
      ``SESSIONFLOW_JARVIS_TTS`` é ``api`` (API hospedada) ou ``piper`` (binário
      CPU standalone, sem GPU — ver ``jarvis.py``), ou quando
      ``SESSIONFLOW_HOST_TTS=1`` — declarado por um host que subiu seu
      PRÓPRIO servidor XTTS local (ex.: Windows/WSL2 com GPU NVIDIA rodando
      ``coqui-tts`` em CUDA na mesma porta que o ``jarvis.py`` já espera).
    - ``transcription``: ``True`` no Mac (mlx-whisper) ou quando
      ``SESSIONFLOW_HOST_TRANSCRIPTION=1`` — declarado por um host com
      backend ``faster-whisper`` configurado (CUDA ou CPU, ver
      ``transcriber.py``/``SESSIONFLOW_FASTER_WHISPER_DEVICE``).

    Esses dois envs são o host "avisando" que já tem a infra local no ar;
    não fazemos probe de rede aqui (capabilities são calculadas 1x no boot).
    """
    tts_mode = os.environ.get("SESSIONFLOW_JARVIS_TTS", "").strip().lower()
    tts_via_config = tts_mode in ("api", "piper")
    tts_local = os.environ.get("SESSIONFLOW_HOST_TTS", "").strip().lower() in ("1", "true")
    transcription_local = os.environ.get(
        "SESSIONFLOW_HOST_TRANSCRIPTION", ""
    ).strip().lower() in ("1", "true")
    is_darwin = plat == "darwin"
    return {
        "platform": plat,
        "tts": is_darwin or tts_via_config or tts_local,
        "transcription": is_darwin or transcription_local,
        "open_terminal": is_darwin,  # "abrir no Mac" via osascript
    }


# --- Hardware/SO detalhado (Perfil > card do host, expandido) ---------------
#
# Tudo aqui é BEST-EFFORT: qualquer probe que falhe (comando ausente, timeout,
# permissão) devolve None pro campo específico — nunca derruba o heartbeat
# inteiro. Calculado 1x no boot (mesmo momento de `capabilities_for`), não a
# cada heartbeat — hardware não muda em runtime, e alguns probes (system_profiler
# no Mac, cmd.exe no WSL) são lentos demais pra rodar em loop curto.


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    """Roda um comando externo, devolve stdout (str) ou None em qualquer falha.

    ``errors="replace"``: ``cmd.exe`` num Windows não-inglês (ex.: PT-BR)
    responde no CODEPAGE OEM local (ex.: CP850/860), não UTF-8 — decodificar
    como UTF-8 estrito derrubava com ``UnicodeDecodeError`` (não é
    ``OSError``, então nem caía no except) assim que a saída tinha um
    acento. Só usamos o texto pra achar um padrão numérico (versão do
    Windows), então caracteres ilegíveis (`�`) no meio não atrapalham.
    """
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _cpu_model() -> str | None:
    system = platform.system().lower()
    if system == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if system == "linux":
        try:
            text = Path("/proc/cpuinfo").read_text()
            m = re.search(r"^model name\s*:\s*(.+)$", text, re.MULTILINE)
            return m.group(1).strip() if m else None
        except OSError:
            return None
    return platform.processor() or None


#: candidatos pro binário do nvidia-smi, na ordem em que tentamos. O nome
#: puro depende do PATH achar; no WSL2 com passthrough de GPU, o binário
#: real fica em ``/usr/lib/wsl/lib`` — caminho que o PATH MÍNIMO de um
#: serviço systemd não inclui (só o shell interativo/``.bashrc`` estende o
#: PATH com os diretórios de interop do WSL). Mesma classe de bug do
#: ``cmd.exe`` em ``_windows_version_from_wsl`` — sem o caminho absoluto,
#: uma GPU NVIDIA de verdade (testado: RTX 3060) nunca era encontrada.
_NVIDIA_SMI_CANDIDATES = ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi")


def _gpu_name() -> str | None:
    """Nome da GPU, se detectável. NVIDIA (Linux/WSL2 com passthrough) via
    ``nvidia-smi``; Mac via ``system_profiler`` (Apple Silicon = GPU integrada
    no chip, então cai no nome do chip). None = sem GPU dedicada detectada ou
    sem ferramenta disponível (não é erro, é o caso comum)."""
    for candidate in _NVIDIA_SMI_CANDIDATES:
        nvidia = _run([candidate, "--query-gpu=name", "--format=csv,noheader"])
        if nvidia:
            return nvidia.splitlines()[0].strip()
    if platform.system().lower() == "darwin":
        out = _run(["system_profiler", "SPDisplaysDataType"], timeout=8.0)
        if out:
            m = re.search(r"Chipset Model:\s*(.+)", out)
            if m:
                return m.group(1).strip()
    return None


def _windows_version_from_wsl() -> str | None:
    """Versão do Windows HOSPEDEIRO, vista de dentro do WSL2 (interop com
    binários do Windows via PATH — ``cmd.exe``/``powershell.exe``). None se a
    interop estiver desligada ou o comando não existir.

    ``cmd.exe /c ver`` responde no IDIOMA do Windows (ex.: "Microsoft Windows
    [versão 10.0.22621.4317]" em PT-BR, não "[Version ...]") — por isso o
    regex busca o PADRÃO NUMÉRICO (x.y.z[.w]) direto, sem depender da palavra
    "Version" em inglês, que nunca batia num Windows em outro idioma.

    Caminho ABSOLUTO do ``cmd.exe`` (não só o nome): o worker roda como
    serviço systemd, cujo ``PATH`` é o mínimo do systemd (sem a extensão do
    WSL que injeta ``/mnt/c/Windows/system32`` — essa vem do shell
    interativo/``.bashrc``, não existe num processo de serviço) — por isso
    ``subprocess.run(["cmd.exe", ...])`` nunca achava o binário nesse
    contexto (silenciosamente, via ``OSError``), mesmo funcionando na mão
    num terminal comum.
    """
    out = _run(["/mnt/c/Windows/system32/cmd.exe", "/c", "ver"], timeout=5.0)
    if out:
        m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", out)
        if m:
            return f"Windows (build {m.group(1)})"
    return None


def _os_detail(plat: str) -> dict[str, Any]:
    """Detalhe do sistema operacional. No WSL2, ``distro`` é a distro Linux
    visível (ex.: Ubuntu) e ``host_os`` é o Windows REAL por baixo — evita a
    confusão "isso aqui é Linux ou é Windows?" que o usuário quer clareada."""
    detail: dict[str, Any] = {"distro": None, "host_os": None}
    if plat == "darwin":
        ver = _run(["sw_vers", "-productVersion"])
        detail["distro"] = f"macOS {ver}" if ver else "macOS"
        return detail
    if plat in ("linux", "wsl2"):
        try:
            text = Path("/etc/os-release").read_text()
            m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
            detail["distro"] = m.group(1).strip() if m else "Linux"
        except OSError:
            detail["distro"] = "Linux"
        if plat == "wsl2":
            detail["host_os"] = _windows_version_from_wsl()
        return detail
    detail["distro"] = f"Windows {platform.release()}".strip()
    return detail


def _disks() -> list[dict[str, Any]]:
    """Discos "reais" com capacidade total/usada em GB.

    Dois filtros de ruído, sem os quais um único disco físico vira vários
    "discos" na lista:

    1. ``fstype`` virtual óbvio (overlay do Docker, tmpfs etc.), os
       containers internos do APFS no macOS (``/System/Volumes/*``, exceto
       ``Data`` que é onde ficam os arquivos do usuário), e os mounts
       internos do WSL2 (``/mnt/wsl/*`` — distros/ferramentas do Docker
       Desktop, não um disco de verdade) — pulados de cara.
    2. Dedup por CAPACIDADE TOTAL: volumes/partições diferentes que
       compartilham o MESMO container físico quase sempre reportam o
       ``total`` idêntico (ex.: "/" e "/System/Volumes/Data" no macOS); dois
       discos físicos DISTINTOS com bytes idênticos de capacidade são
       praticamente impossíveis, então isso é seguro.

    Best-effort: um disco que falhar ao consultar (ex.: unidade de rede
    offline) é pulado, não derruba os demais.
    """
    skip_fstypes = {"tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs"}
    seen_totals: set[float] = set()
    disks: list[dict[str, Any]] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:  # noqa: BLE001 - best-effort
        return disks
    for part in partitions:
        if part.fstype in skip_fstypes:
            continue
        if part.mountpoint.startswith("/System/Volumes/") and part.mountpoint != "/System/Volumes/Data":
            continue
        if part.mountpoint.startswith("/mnt/wsl/"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        total_gb = round(usage.total / (1024**3), 1)
        if total_gb in seen_totals:
            continue
        seen_totals.add(total_gb)
        disks.append(
            {
                "mount": part.mountpoint,
                "total_gb": total_gb,
                "used_gb": round(usage.used / (1024**3), 1),
            }
        )
    return disks


def hardware_info(plat: str) -> dict[str, Any]:
    """Snapshot de hardware/SO deste host — CPU/RAM/GPU/disco/versão do SO
    (com o Windows real por trás do WSL2, quando aplicável). Calculado 1x no
    boot do worker; ver docstring da seção acima sobre a filosofia best-effort.
    """
    info: dict[str, Any] = {
        "cpu_model": None,
        "cpu_cores": None,
        "ram_total_gb": None,
        "gpu": None,
        "os_detail": {},
        "disks": [],
    }
    try:
        info["cpu_model"] = _cpu_model()
        info["cpu_cores"] = psutil.cpu_count(logical=True)
        info["ram_total_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("hardware_info: cpu/ram falhou", exc_info=True)
    try:
        info["gpu"] = _gpu_name()
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("hardware_info: gpu falhou", exc_info=True)
    try:
        info["os_detail"] = _os_detail(plat)
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("hardware_info: os_detail falhou", exc_info=True)
    try:
        info["disks"] = _disks()
    except Exception:  # noqa: BLE001 - best-effort
        logger.debug("hardware_info: disks falhou", exc_info=True)
    return info
