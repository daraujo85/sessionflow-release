"""Read endpoints: Worker status (`GET /worker`, `GET /workers`) e limites de
uso (`GET /usage`).

Cada Worker (1 por host, AD-011) faz heartbeat em ``worker_status`` — 1 doc
por host (``_id=host_id``), com hostname/platform/capabilities/started_at/
updated_at — e raspa o ``/usage`` do Claude em ``host_usage``. Estes
endpoints expõem esses dados REAIS para o Perfil — nada é fabricado.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.publishers.command_publisher import publish_command

# Motores de síntese suportados pelo worker (ver worker/sessionflow_worker/jarvis.py).
_TTS_MODES = {"say", "xtts", "piper", "api"}

router = APIRouter(tags=["worker"])

WORKER_STATUS_COLLECTION = "worker_status"
HOST_USAGE_COLLECTION = "host_usage"
HOST_USAGE_KEY = "host"
# updated_at mais velho que isto ⇒ worker considerado offline.
ONLINE_WINDOW_SECONDS = 30.0


class WorkerOut(BaseModel):
    """Status de UM Worker/host para o card do Perfil."""

    online: bool = False
    hostname: str | None = None
    # Nome de EXIBIÇÃO do host (editável via PUT /workers/{host_id}/display-name),
    # ex. "Notebook do Diego" em vez de "DESKTOP-ASCBQRT". None = usa o
    # ``hostname`` técnico como fallback (comportamento de hoje).
    display_name: str | None = None
    # Emoji do host (editável junto do nome) — ex. "🦆" pro Windows, "🍎" pro
    # Mac. Vira o identificador visual nos badges (substitui o ícone
    # genérico) — diferencia tarefa/sessão por host num relance.
    emoji: str | None = None
    # Multi-host (AD-011): identidade + o que esse host consegue fazer.
    # ``None`` em docs antigos (pré-migração, worker ainda não reiniciou).
    host_id: str | None = None
    platform: str | None = None
    capabilities: dict | None = None
    uptime_seconds: float | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    # Áudio do JARVIS (Perfil > Áudio), editável via PUT /workers/{host_id}/audio-settings.
    # None = segue a env var do host (SESSIONFLOW_JARVIS_TTS / efeito ligado).
    tts_mode: str | None = None
    voice_effect: bool | None = None
    # Hardware/SO detalhado (Perfil > card do host, expandido) — calculado 1x
    # no boot do worker (ver worker/sessionflow_worker/host_identity.py).
    hardware: dict | None = None


class WorkerDisplayName(BaseModel):
    """Nome/emoji de exibição do host — vazio/None limpa (volta ao default)."""

    display_name: str | None = None
    emoji: str | None = None


class WorkerAudioSettings(BaseModel):
    """Config de áudio do JARVIS deste host — None em cada campo = usa o
    default do host (env var / efeito ligado)."""

    tts_mode: str | None = None
    voice_effect: bool | None = None


class ClaudeLimits(BaseModel):
    """Limites reais do Claude (scrape do /usage)."""

    session_pct: float | None = None
    session_reset: str | None = None
    week_pct: float | None = None
    week_reset: str | None = None


class UsageOut(BaseModel):
    """Limites de uso por provider. Hoje só o Claude expõe uso real."""

    claude: ClaudeLimits | None = None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_worker_out(doc: dict) -> WorkerOut:
    now = datetime.now(timezone.utc)
    updated = _aware(doc.get("updated_at"))
    started = _aware(doc.get("started_at"))
    online = updated is not None and (now - updated).total_seconds() < ONLINE_WINDOW_SECONDS
    uptime = (now - started).total_seconds() if (online and started) else None
    return WorkerOut(
        online=online,
        hostname=doc.get("hostname"),
        display_name=doc.get("display_name"),
        emoji=doc.get("emoji"),
        host_id=doc.get("_id") if doc.get("_id") != "worker" else None,
        platform=doc.get("platform"),
        capabilities=doc.get("capabilities"),
        uptime_seconds=uptime,
        started_at=started,
        updated_at=updated,
        tts_mode=doc.get("tts_mode"),
        voice_effect=doc.get("voice_effect"),
        hardware=doc.get("hardware"),
    )


@router.get("/worker", response_model=WorkerOut)
async def get_worker(request: Request) -> WorkerOut:
    """Status de UM worker — retrocompat (telas que ainda não sabem de
    multi-host). Multi-host (AD-011): não existe mais um único ``_id="worker"``
    fixo — pega o de ``updated_at`` mais recente (aproximação de "o host mais
    ativo agora"). Use `GET /workers` pra ver TODOS."""
    db = request.app.state.mongo_db
    doc = await db[WORKER_STATUS_COLLECTION].find_one(
        {}, sort=[("updated_at", -1)]
    )
    if not doc:
        return WorkerOut(online=False)
    return _to_worker_out(doc)


@router.get("/workers", response_model=list[WorkerOut])
async def list_workers(request: Request) -> list[WorkerOut]:
    """Status de TODOS os workers/hosts conhecidos (multi-host, AD-011).

    Base pro badge/filtro de host no frontend (fase futura do plano
    multi-host) — hoje sem consumidor na UI, mas já exposto pra inspeção/testes.
    """
    db = request.app.state.mongo_db
    docs = await db[WORKER_STATUS_COLLECTION].find({}).sort("updated_at", -1).to_list(
        length=50
    )
    return [_to_worker_out(doc) for doc in docs]


@router.put("/workers/{host_id}/display-name", response_model=WorkerOut)
async def set_worker_display_name(
    request: Request, host_id: str, body: WorkerDisplayName
) -> WorkerOut:
    """Define/limpa o nome e o emoji de exibição de um host (Perfil). Não
    mexe no ``hostname`` técnico — só o rótulo/ícone mostrados no app.
    Vazio/None em cada campo limpa (volta ao default: hostname / ícone
    genérico). O request manda os DOIS campos juntos (mesmo editando só um
    na UI) — evita que salvar o nome apague o emoji já definido, e vice-versa."""
    db = request.app.state.mongo_db
    coll = db[WORKER_STATUS_COLLECTION]
    name = (body.display_name or "").strip()[:60] or None
    # Emoji: aceita só 1-2 "caracteres" visuais (emoji simples ou composto
    # com modificador/ZWJ) — corta agressivo pra não virar um textão no lugar
    # de um ícone. Não valida que É emoji de fato (best-effort, é cosmético).
    emoji = (body.emoji or "").strip()[:8] or None
    doc = await coll.find_one_and_update(
        {"_id": host_id},
        {"$set": {"display_name": name, "emoji": emoji}},
        return_document=True,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Host not found")
    return _to_worker_out(doc)


@router.put("/workers/{host_id}/audio-settings", response_model=WorkerOut)
async def set_worker_audio_settings(
    request: Request, host_id: str, body: WorkerAudioSettings
) -> WorkerOut:
    """Define o modo de TTS/efeito de voz do JARVIS deste host (Perfil > Áudio).

    ``tts_mode`` em branco/None volta a seguir a env var do host
    (``SESSIONFLOW_JARVIS_TTS``) — não força nada. Mesma ideia pro
    ``voice_effect`` (None = liga, comportamento default de sempre).
    """
    if body.tts_mode is not None and body.tts_mode not in _TTS_MODES:
        raise HTTPException(status_code=422, detail=f"tts_mode inválido: {body.tts_mode!r}")
    db = request.app.state.mongo_db
    coll = db[WORKER_STATUS_COLLECTION]
    doc = await coll.find_one_and_update(
        {"_id": host_id},
        {"$set": {"tts_mode": body.tts_mode, "voice_effect": body.voice_effect}},
        return_document=True,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Host not found")
    return _to_worker_out(doc)


@router.post("/workers/{host_id}/jarvis-test", status_code=202)
async def jarvis_test_voice(request: Request, host_id: str) -> dict:
    """Pede pro worker DESTE host sintetizar e tocar uma frase de teste —
    mesmo pipeline real (resumo/voz/efeito), botão "Testar voz" do Perfil."""
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="jarvis_test", payload={}, host_id=host_id
    )
    return {"status": "queued", "command_id": command_id}


@router.get("/usage", response_model=UsageOut)
async def get_usage(request: Request) -> UsageOut:
    db = request.app.state.mongo_db
    doc = await db[HOST_USAGE_COLLECTION].find_one({"key": HOST_USAGE_KEY})
    if not doc:
        return UsageOut(claude=None)
    return UsageOut(
        claude=ClaudeLimits(
            session_pct=doc.get("session_pct"),
            session_reset=doc.get("session_reset"),
            week_pct=doc.get("week_pct"),
            week_reset=doc.get("week_reset"),
        )
    )
