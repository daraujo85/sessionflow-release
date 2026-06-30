"""Session endpoints: ``GET /sessions``, ``GET /sessions/{id}`` and
``POST /sessions``.

Implements visibility / filtering (TMUX-01/03/12) and session creation
(TMUX-05/06/07): creation validates the request, performs an optimistic
duplicate check against Mongo and publishes a ``create`` command to RabbitMQ.
Session documents are serialized from Mongo: ``_id`` (ObjectId) -> ``id``
(string); datetimes are emitted as ISO-8601 by Pydantic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app import share
from app.publishers.command_publisher import publish_command
from app.repositories.sessions_repo import SessionsRepository
from app.repositories.uploads_repo import UploadsRepository

# Validade do link compartilhável: além de morrer ao parar/apagar a sessão, o
# link caduca sozinho depois disso (segurança).
SHARE_TTL = timedelta(hours=24)

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Module-level singleton so the ``File`` call is not evaluated in the function
# signature default (ruff B008).
_AUDIO_FILE = File(...)
_CAPTION_FORM = Form(None)


class SessionOut(BaseModel):
    """Serialized session document returned by the API."""

    id: str
    tmux_name: str | None = None
    display_name: str | None = None
    agent_type: str | None = None
    model: str | None = None
    effort: str | None = None
    work_dir: str | None = None
    status: str | None = None
    # Rótulo fino do que o agente está fazendo (derivado da tela pelo worker).
    activity: str | None = None
    origin: str | None = None
    tmux_session_id: str | None = None
    agent_pid: int | None = None
    last_seen_at: datetime | None = None
    last_activity_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Métricas reais da janela de contexto (sessões Claude); None se indisponível.
    metrics: dict[str, Any] | None = None
    # Sessão favoritada (preferência do usuário; some das listas se desmarcada).
    favorite: bool = False
    # JARVIS: resumo falado da sessão (voz no celular) quando conclui/aguarda.
    jarvis: bool = False

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> SessionOut:
        data = dict(doc)
        data["id"] = str(data.pop("_id"))
        return cls.model_validate(data)


class SessionListOut(BaseModel):
    """Envelope for the session list."""

    items: list[SessionOut] = Field(default_factory=list)
    total: int = 0


class AgentType(StrEnum):
    """Supported agent types for a session."""

    claude = "claude"
    codex = "codex"
    gemini = "gemini"
    opencode = "opencode"


class SessionCreate(BaseModel):
    """Request body for creating a session (TMUX-05/06/07)."""

    name: str = Field(min_length=1)
    display_name: str | None = None
    agent_type: AgentType
    work_dir: str = Field(min_length=1)
    model: str | None = None
    effort: str | None = None


class SessionCreateAccepted(BaseModel):
    """Response for an accepted session-create command."""

    command_id: str
    status: str = "accepted"


class AudioUploadAccepted(BaseModel):
    """Response for an accepted audio upload (DASH-14)."""

    command_id: str
    upload_id: str
    status: str = "accepted"


class SessionInput(BaseModel):
    """Request body for sending text input to a session (DASH-13).

    ``text`` must be a non-empty string (empty/whitespace -> 422).
    ``enter`` (default True) anexa Enter; o modo "ao vivo" usa False para
    encaminhar o que está sendo digitado sem submeter.
    """

    text: str = Field(min_length=1)
    enter: bool = True


# Teclas especiais aceitas para navegar prompts TUI (espelhadas no Worker).
ALLOWED_KEYS = frozenset(
    {
        "up",
        "down",
        "left",
        "right",
        "enter",
        "space",
        "escape",
        "esc",
        "tab",
        "backspace",
        "ctrl-c",
        "scroll-up",
        "scroll-down",
    }
)


class SessionKey(BaseModel):
    """Request body para enviar uma TECLA ESPECIAL a uma sessão.

    ``key`` deve pertencer a {@link ALLOWED_KEYS} (ex.: ``up``, ``enter``,
    ``space``, ``escape``). Diferente de ``/input`` (texto + Enter), serve para
    navegar pickers/listas TUI dos agentes.
    """

    key: str = Field(min_length=1)


class SessionResize(BaseModel):
    """Request body para redimensionar o pane do tmux (colunas×linhas).

    O cliente informa quantas colunas/linhas cabem na sua área de terminal; o
    worker força esse tamanho (``window-size manual``) e o agente reflui — assim
    o terminal usa a largura toda em telas grandes.
    """

    cols: int = Field(ge=20, le=500)
    rows: int = Field(ge=5, le=300)


class SessionRename(BaseModel):
    """Request body for renaming a session (TMUX-10).

    Accepts either ``name`` or ``new_name`` as the new session name.
    """

    name: str | None = None
    new_name: str | None = None

    @property
    def resolved_name(self) -> str | None:
        return self.new_name if self.new_name is not None else self.name


def _get_repo(request: Request) -> SessionsRepository:
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    return SessionsRepository(db, settings.sessions_collection)


# Prefixos de sessões INTERNAS do worker (scraping efêmero) — nunca exibidas.
_INTERNAL_PREFIXES = ("sfusage-", "sfmodel-", "sftest-")


@router.get("", response_model=SessionListOut)
async def list_sessions(
    request: Request,
    status: str | None = Query(
        default=None,
        description="Filter by exact session status (e.g. running, completed, detached).",
    ),
) -> SessionListOut:
    repo = _get_repo(request)
    docs = await repo.list_sessions(status=status)
    # Esconde as sessões INTERNAS efêmeras de scraping do worker (lê limites de
    # uso e lista de modelos abrindo o `claude`, mostram a tela de estatística e
    # morrem). Não são sessões do usuário — não devem aparecer na lista.
    items = [
        SessionOut.from_doc(doc)
        for doc in docs
        if not str(doc.get("tmux_name") or "").startswith(_INTERNAL_PREFIXES)
    ]
    return SessionListOut(items=items, total=len(items))


@router.post("", response_model=SessionCreateAccepted, status_code=202)
async def create_session(request: Request, body: SessionCreate) -> SessionCreateAccepted:
    name = body.name.strip()
    work_dir = body.work_dir.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if not work_dir:
        raise HTTPException(status_code=422, detail="work_dir must not be empty")

    # Gemini has no effort dimension; force it to None regardless of input.
    effort = None if body.agent_type == AgentType.gemini else body.effort

    repo = _get_repo(request)
    if await repo.active_with_name_exists(name):
        raise HTTPException(
            status_code=409,
            detail=f"An active session named '{name}' already exists",
        )

    payload = {
        "name": name,
        "display_name": (body.display_name or "").strip() or None,
        "agent_type": body.agent_type.value,
        "work_dir": work_dir,
        "model": body.model,
        "effort": effort,
    }
    settings = request.app.state.settings
    command_id = await publish_command(settings, type="create", payload=payload)
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(request: Request, session_id: str) -> SessionOut:
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionOut.from_doc(doc)


class ShareLinkOut(BaseModel):
    """Estado do link compartilhável de uma sessão."""

    active: bool = False
    url: str | None = None
    expires_at: datetime | None = None


def _share_url(request: Request, session_id: str, token: str) -> str:
    """Monta a URL pública do link (origem do frontend + rota guest /s/:id)."""
    origin = (request.app.state.settings.rp_origin or "").rstrip("/")
    if not origin:
        # Fallback: deriva da própria request (dev/local).
        origin = str(request.base_url).rstrip("/")
    return f"{origin}/s/{session_id}?k={token}"


@router.get("/{session_id}/share", response_model=ShareLinkOut)
async def get_share_link(request: Request, session_id: str) -> ShareLinkOut:
    """Estado atual do link (dono): ativo? URL? validade?"""
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not share.token_valid(doc, doc.get("share_token")):
        return ShareLinkOut(active=False)
    return ShareLinkOut(
        active=True,
        url=_share_url(request, session_id, str(doc["share_token"])),
        expires_at=doc.get("share_expires_at"),
    )


@router.post("/{session_id}/share", response_model=ShareLinkOut, status_code=201)
async def create_share_link(request: Request, session_id: str) -> ShareLinkOut:
    """Gera (ou rotaciona) o link compartilhável da sessão. Vale 24h, morre se a
    sessão for parada/apagada, e pode ser revogada (DELETE)."""
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")
    token = share.new_token()
    expires_at = datetime.now(UTC) + SHARE_TTL
    await repo.set_share(session_id, token, expires_at)
    return ShareLinkOut(
        active=True,
        url=_share_url(request, session_id, token),
        expires_at=expires_at,
    )


@router.delete("/{session_id}/share", response_model=ShareLinkOut)
async def revoke_share_link(request: Request, session_id: str) -> ShareLinkOut:
    """Revoga o link na hora (mesmo com a sessão viva)."""
    repo = _get_repo(request)
    await repo.clear_share(session_id)
    return ShareLinkOut(active=False)


async def _require_tmux_name(request: Request, session_id: str) -> str:
    """Fetch a session by id and return its ``tmux_name`` or raise 404."""
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return doc["tmux_name"]


@router.delete("/{session_id}", response_model=SessionCreateAccepted, status_code=202)
async def kill_session(request: Request, session_id: str) -> SessionCreateAccepted:
    """Encerra (para) a sessão: mata o tmux mas MANTÉM o registro (histórico)."""
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="kill", payload={"name": tmux_name}
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.delete(
    "/{session_id}/purge", response_model=SessionCreateAccepted, status_code=202
)
async def purge_session(request: Request, session_id: str) -> SessionCreateAccepted:
    """ELIMINA a sessão de vez: mata o tmux (se vivo) e REMOVE o registro +
    dados relacionados. Some do app e do host (diferente de encerrar)."""
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    # Remove o registro NA HORA (some da lista imediatamente, sem flicker de
    # 'apaguei e voltou'); o worker ainda mata o tmux no host e limpa os dados
    # relacionados (tasks/output/events/screen) ao processar o comando.
    repo = _get_repo(request)
    await repo.delete_session(session_id)
    command_id = await publish_command(
        settings, type="delete", payload={"name": tmux_name}
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


class SessionFavorite(BaseModel):
    """Marca/desmarca a sessão como favorita."""

    favorite: bool


@router.put("/{session_id}/favorite", status_code=200)
async def set_favorite(
    request: Request, session_id: str, body: SessionFavorite
) -> dict:
    """Favorita/desfavorita a sessão (preferência persistida no doc)."""
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.sessions_collection].update_one(
        {"tmux_name": tmux_name}, {"$set": {"favorite": body.favorite}}
    )
    return {"favorite": body.favorite}


class SessionJarvis(BaseModel):
    """Liga/desliga o resumo falado (JARVIS) por sessão."""

    jarvis: bool


@router.put("/{session_id}/jarvis", status_code=200)
async def set_jarvis(
    request: Request, session_id: str, body: SessionJarvis
) -> dict:
    """Liga/desliga o JARVIS (voz) para esta sessão (persistido no doc).

    O worker lê esse campo (ou ``app_settings.jarvis_all``) para decidir se fala.
    """
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    await db[settings.sessions_collection].update_one(
        {"tmux_name": tmux_name}, {"$set": {"jarvis": body.jarvis}}
    )
    return {"jarvis": body.jarvis}


@router.patch("/{session_id}", response_model=SessionCreateAccepted, status_code=202)
async def rename_session(
    request: Request, session_id: str, body: SessionRename
) -> SessionCreateAccepted:
    """Rename a session's tmux session (TMUX-10)."""
    new_name = (body.resolved_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="new name must not be empty")

    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="rename", payload={"old": tmux_name, "new": new_name}
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.post(
    "/{session_id}/input", response_model=SessionCreateAccepted, status_code=202
)
async def send_input(
    request: Request, session_id: str, body: SessionInput
) -> SessionCreateAccepted:
    """Send text input to a session's tmux pane (DASH-13).

    Com ``enter=True`` (submeter) fazemos ``strip()`` e rejeitamos vazio. Com
    ``enter=False`` (modo ao vivo) preservamos o texto cru — um delta pode ser
    só um espaço ou ``/``, que são significativos.
    """
    text = body.text if not body.enter else body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")

    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings,
        type="input",
        payload={"name": tmux_name, "text": text, "enter": body.enter},
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.post(
    "/{session_id}/key", response_model=SessionCreateAccepted, status_code=202
)
async def send_key(
    request: Request, session_id: str, body: SessionKey
) -> SessionCreateAccepted:
    """Envia uma tecla especial (seta/enter/espaço/esc/tab) ao pane da sessão.

    Permite navegar prompts TUI dos agentes (pickers, listas de seleção) a
    partir do app, onde não há teclado físico para essas teclas.
    """
    key = body.key.strip().lower()
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=422, detail=f"key inválida: {body.key!r}")

    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="key", payload={"name": tmux_name, "key": key}
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.post(
    "/{session_id}/resize", response_model=SessionCreateAccepted, status_code=202
)
async def resize_session(
    request: Request, session_id: str, body: SessionResize
) -> SessionCreateAccepted:
    """Redimensiona o pane do tmux p/ caber na área do cliente (reflow do agente)."""
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings,
        type="resize",
        payload={"name": tmux_name, "cols": body.cols, "rows": body.rows},
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.post("/{session_id}/instruct-milestones")
async def instruct_milestones(request: Request, session_id: str) -> dict:
    """Injeta (1x) a instrução de trabalhar em tarefas/marcos na sessão.

    Idempotente e gated pelo setting global ``milestones_auto``:
    - setting desligado → no-op (``skipped``);
    - sessão já instruída (``milestones_instructed_at``) → ``already``;
    - senão → publica a instrução como input e marca o flag (``instructed``).
    Chamado pelo app ao ABRIR a sessão (cobre novas e as que já rodam).
    """
    from app.routers.settings import milestones_instruction, read_settings

    cfg = await read_settings(request)
    if not cfg.milestones_auto:
        return {"status": "skipped", "reason": "disabled"}

    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if doc.get("milestones_instructed_at"):
        return {"status": "already"}

    tmux_name = doc["tmux_name"]
    settings = request.app.state.settings
    db = request.app.state.mongo_db
    command_id = await publish_command(
        settings,
        type="input",
        payload={
            "name": tmux_name,
            "text": milestones_instruction(tmux_name),
            "enter": True,
        },
    )
    await db[settings.sessions_collection].update_one(
        {"tmux_name": tmux_name},
        {"$set": {"milestones_instructed_at": datetime.now(UTC)}},
    )
    return {"status": "instructed", "command_id": command_id}


@router.post(
    "/{session_id}/audio", response_model=AudioUploadAccepted, status_code=202
)
async def upload_audio(
    request: Request, session_id: str, file: UploadFile = _AUDIO_FILE
) -> AudioUploadAccepted:
    """Upload an audio file for a session (DASH-14).

    Persists the file under ``{uploads_dir}/{session_id}/{uuid}.{ext}``,
    records an ``uploads`` document and publishes an ``audio`` command so the
    Worker can pick the file up.
    """
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings

    ext = Path(file.filename or "").suffix.lstrip(".") or "bin"
    target_dir = Path(settings.uploads_dir) / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{uuid.uuid4().hex}.{ext}"
    target_path.write_bytes(await file.read())
    path_str = str(target_path)

    db = request.app.state.mongo_db
    uploads_repo = UploadsRepository(db, settings.uploads_collection)
    upload_id = await uploads_repo.create_upload(
        session_id=session_id, path=path_str, kind="audio", status="received"
    )

    command_id = await publish_command(
        settings,
        type="audio",
        payload={"name": tmux_name, "path": path_str, "upload_id": upload_id},
    )
    return AudioUploadAccepted(
        command_id=command_id, upload_id=upload_id, status="accepted"
    )


@router.post(
    "/{session_id}/file", response_model=AudioUploadAccepted, status_code=202
)
async def upload_file(
    request: Request,
    session_id: str,
    file: UploadFile = _AUDIO_FILE,
    caption: str | None = _CAPTION_FORM,
) -> AudioUploadAccepted:
    """Anexa um arquivo/imagem à sessão: salva no host e injeta o caminho.

    Persiste em ``{uploads_dir}/{session_id}/{uuid}.{ext}`` e publica um comando
    ``file`` — o Worker re-rooteia o caminho para o host e injeta no pane (o
    agente lê a imagem/arquivo pelo path). ``caption`` opcional é o texto que
    acompanha o anexo — vai junto na mesma injeção (imagem + texto de uma vez).
    """
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings

    original = Path(file.filename or "").name or "arquivo"
    ext = Path(original).suffix.lstrip(".") or "bin"
    target_dir = Path(settings.uploads_dir) / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{uuid.uuid4().hex}.{ext}"
    target_path.write_bytes(await file.read())
    path_str = str(target_path)

    db = request.app.state.mongo_db
    uploads_repo = UploadsRepository(db, settings.uploads_collection)
    upload_id = await uploads_repo.create_upload(
        session_id=session_id, path=path_str, kind="file", status="received"
    )

    caption_clean = (caption or "").strip()
    command_id = await publish_command(
        settings,
        type="file",
        payload={
            "name": tmux_name,
            "path": path_str,
            "filename": original,
            "upload_id": upload_id,
            "caption": caption_clean or None,
        },
    )
    return AudioUploadAccepted(
        command_id=command_id, upload_id=upload_id, status="accepted"
    )


@router.post(
    "/{session_id}/resume", response_model=SessionCreateAccepted, status_code=202
)
async def resume_session(request: Request, session_id: str) -> SessionCreateAccepted:
    """Resume a detached/stopped session (TMUX-11)."""
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="resume", payload={"name": tmux_name}
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


@router.post(
    "/{session_id}/open-terminal",
    response_model=SessionCreateAccepted,
    status_code=202,
)
async def open_terminal(request: Request, session_id: str) -> SessionCreateAccepted:
    """Abre a sessão num Terminal do Mac (``tmux attach``) p/ uso lado a lado.

    O worker (no Mac) abre o Terminal.app já anexado à sessão tmux — vários
    clientes podem anexar a mesma sessão (espelhada), então app e terminal
    mostram o MESMO conteúdo.
    """
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    # Título amigável (display name) p/ a aba do terminal — facilita achar.
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    title = (
        (doc.get("display_name") or doc.get("tmux_name") or tmux_name)
        if doc
        else tmux_name
    )
    command_id = await publish_command(
        settings,
        type="open_terminal",
        payload={"name": tmux_name, "title": title},
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")
