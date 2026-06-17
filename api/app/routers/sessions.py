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
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app.publishers.command_publisher import publish_command
from app.repositories.sessions_repo import SessionsRepository
from app.repositories.uploads_repo import UploadsRepository

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Module-level singleton so the ``File`` call is not evaluated in the function
# signature default (ruff B008).
_AUDIO_FILE = File(...)


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
    origin: str | None = None
    tmux_session_id: str | None = None
    agent_pid: int | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Métricas reais da janela de contexto (sessões Claude); None se indisponível.
    metrics: dict[str, Any] | None = None

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
    }
)


class SessionKey(BaseModel):
    """Request body para enviar uma TECLA ESPECIAL a uma sessão.

    ``key`` deve pertencer a {@link ALLOWED_KEYS} (ex.: ``up``, ``enter``,
    ``space``, ``escape``). Diferente de ``/input`` (texto + Enter), serve para
    navegar pickers/listas TUI dos agentes.
    """

    key: str = Field(min_length=1)


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
    items = [SessionOut.from_doc(doc) for doc in docs]
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


async def _require_tmux_name(request: Request, session_id: str) -> str:
    """Fetch a session by id and return its ``tmux_name`` or raise 404."""
    repo = _get_repo(request)
    doc = await repo.get_session(session_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return doc["tmux_name"]


@router.delete("/{session_id}", response_model=SessionCreateAccepted, status_code=202)
async def kill_session(request: Request, session_id: str) -> SessionCreateAccepted:
    """Kill a session's tmux process (TMUX-09)."""
    tmux_name = await _require_tmux_name(request, session_id)
    settings = request.app.state.settings
    command_id = await publish_command(
        settings, type="kill", payload={"name": tmux_name}
    )
    return SessionCreateAccepted(command_id=command_id, status="accepted")


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
        {"$set": {"milestones_instructed_at": datetime.now(timezone.utc)}},
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
