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

    Espelha o mapeamento do ``PORTABILITY.md``: TTS/transcrição/"abrir no
    Mac" são macOS-only hoje. ``tts`` também é ``True`` fora do Mac quando
    ``SESSIONFLOW_JARVIS_TTS=api`` está setado (a API hospedada funciona de
    qualquer plataforma — só o fallback local ``say``/XTTS é mac-only).
    """
    tts_via_api = os.environ.get("SESSIONFLOW_JARVIS_TTS", "").strip().lower() == "api"
    is_darwin = plat == "darwin"
    return {
        "platform": plat,
        "tts": is_darwin or tts_via_api,
        "transcription": is_darwin,  # mlx-whisper é Apple Silicon only (hoje)
        "open_terminal": is_darwin,  # "abrir no Mac" via osascript
    }
