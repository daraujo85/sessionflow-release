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
# updated_at mais velho que isto ⇒ worker considerado offline. 3× o
# HEARTBEAT_INTERVAL do worker (30s): o ciclo gasta tempo coletando métricas
# antes do sleep, então janela == intervalo fazia host carregado piscar offline.
ONLINE_WINDOW_SECONDS = 90.0
# Janela em que o online=True gravado por POST /start vale sem heartbeat (boot do Colab).
START_GRACE_SECONDS = 600.0

# Notebook Colab padrão — usado quando o `worker_status` não tem
# `colab_notebook_id` próprio. Cada Colab novo (3, 4, ...) pode sobrescrever
# no Mongo via `db.worker_status.update_one({_id: host_id}, {$set:
# {colab_notebook_id: "1ABC..."}})`. Mesmo raciocínio pro `colab_authuser`
# (qual conta Google selecionar quando há múltiplas logadas).
DEFAULT_COLAB_NOTEBOOK_ID = "1WRlvNXyQGyYZ9xURL5gN-7ZmmdHRBi7A"


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
    # Disponibilidade de cada motor de TTS NESTE host — {motor: {installed,
    # installable}} (ver worker/sessionflow_worker/jarvis.tts_engine_status).
    tts_engines: dict | None = None
    # Modelos Ollama instalados NESTE host + quais estão carregados agora
    # (ver worker/sessionflow_worker/host_metrics.fetch_ollama_models).
    # None/[] = Ollama offline ou sem modelos.
    ollama_models: list[dict] | None = None
    # Carga atual (cpu_pct/mem_pct/mem_total_gb/mem_avail_gb) e CLIs de agente
    # instaladas ({claude: bool, ...}) — base do `sf delegate --host auto`
    # (ver worker/sessionflow_worker/host_metrics.sample_load/installed_agents).
    # None = worker antigo.
    metrics: dict | None = None
    agents: dict | None = None
    # Config de URL/auth do Google Colab (Perfil > Ligar/Desligar Colab).
    # `colab_notebook_id` = ID do Drive do notebook pra esta conta Colab
    # (None = usa DEFAULT_COLAB_NOTEBOOK_ID). `colab_authuser` = qual conta
    # Google selecionar (None = heurística: "colab 2"/"02151543" → 1, resto → 0).
    colab_notebook_id: str | None = None
    colab_authuser: str | None = None


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
    age = (now - updated).total_seconds() if updated is not None else None
    if doc.get("online") is False:
        online = False
    elif doc.get("online") is True:
        # `start` grava online=True + updated_at=agora pro Colab que está subindo.
        # Vale só durante o boot; depois disso manda o heartbeat — senão host
        # morto fica 🟢 pra sempre e o --host auto manda sessão pra ele.
        online = age is not None and age < START_GRACE_SECONDS
    else:
        online = age is not None and age < ONLINE_WINDOW_SECONDS
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
        tts_engines=doc.get("tts_engines"),
        ollama_models=doc.get("ollama_models"),
        metrics=doc.get("metrics"),
        agents=doc.get("agents"),
        colab_notebook_id=doc.get("colab_notebook_id"),
        colab_authuser=doc.get("colab_authuser"),
    )


def _is_colab_host(doc: dict, host_id: str) -> bool:
    """Heurística: considera Colab se o nome/hostname ou o host_id contém
    ``colab``. Hosts não-Colab NÃO ganham ``colab_url`` (evita abrir URL do
    Google Drive em máquinas que nem rodam Colab)."""
    name = (doc.get("display_name") or doc.get("hostname") or "").lower()
    return "colab" in name or "colab" in host_id.lower()


def build_colab_url(doc: dict, host_id: str) -> str | None:
    """Monta a URL do notebook Google Colab pra este host. None se não for
    Colab. Ordem de resolução:

    1. notebook_id: ``doc["colab_notebook_id"]`` (string) senão
       ``DEFAULT_COLAB_NOTEBOOK_ID``;
    2. authuser: ``doc["colab_authuser"]`` (string: "0" | "1" | ...) senão
       heurística legada (``colab 2`` / ``colab2`` / host_id ``02151543`` → 1).

    Heurística vive aqui pra ser fácil de testar sem precisar de Mongo.
    """
    if not _is_colab_host(doc, host_id):
        return None
    notebook_id = doc.get("colab_notebook_id") or DEFAULT_COLAB_NOTEBOOK_ID
    authuser = doc.get("colab_authuser")
    if not authuser:
        name = (doc.get("display_name") or doc.get("hostname") or "").lower()
        authuser = (
            "1"
            if ("colab 2" in name or "colab2" in name or "02151543" in host_id)
            else "0"
        )
    return (
        f"https://colab.research.google.com/drive/{notebook_id}?authuser={authuser}"
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


@router.post("/workers/{host_id}/stop", response_model=WorkerOut)
async def stop_worker(request: Request, host_id: str) -> WorkerOut:
    """Marca um worker/host como desligado (offline) no MongoDB."""
    db = request.app.state.mongo_db
    coll = db[WORKER_STATUS_COLLECTION]
    epoch_zero = datetime(1970, 1, 1, tzinfo=timezone.utc)
    doc = await coll.find_one_and_update(
        {"_id": host_id},
        {"$set": {"online": False, "updated_at": epoch_zero}},
        return_document=True,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Host not found")
    return _to_worker_out(doc)


@router.post("/workers/{host_id}/start")
async def start_worker(request: Request, host_id: str) -> dict:
    """Retorna os dados e URL do notebook Google Colab para inicialização da máquina."""
    db = request.app.state.mongo_db
    coll = db[WORKER_STATUS_COLLECTION]
    now = datetime.now(timezone.utc)
    doc = await coll.find_one_and_update(
        {"_id": host_id},
        {"$set": {"online": True, "updated_at": now}},
        return_document=True,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Host not found")

    return {
        "status": "starting",
        "host_id": host_id,
        # None quando não é Colab (heurística em build_colab_url). Frontend
        # usa `res.colab_url` direto — sem reconstruir a URL do lado dele.
        "colab_url": build_colab_url(doc, host_id),
        "display_name": doc.get("display_name") or host_id,
    }


@router.post("/workers/{host_id}/install-tts-engine", status_code=202)
async def install_tts_engine(request: Request, host_id: str, engine: str) -> dict:
    """Pede pro worker DESTE host baixar/instalar um motor de TTS que ainda
    não está presente (hoje só ``piper`` é auto-instalável — ver
    ``jarvis.tts_engine_status``/``install_piper_sync``). Botão "Instalar"
    do Perfil > Áudio, quando o motor escolhido não está pronto no host."""
    if engine != "piper":
        raise HTTPException(
            status_code=422, detail=f"motor não instalável automaticamente: {engine!r}"
        )
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="jarvis_install_piper", payload={}, host_id=host_id
    )
    return {"status": "queued", "command_id": command_id}


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
