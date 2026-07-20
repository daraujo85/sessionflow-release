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

import os
import platform
import uuid
from pathlib import Path

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
