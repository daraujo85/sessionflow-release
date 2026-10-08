"""Webhook inbound do JARVIS — entrega o áudio falado no celular via SSE.

O JARVIS (hook do Claude Code) em modo ``away`` faz POST do resumo já
sintetizado (áudio em base64) para um webhook configurável. Apontando esse
webhook para ``POST /jarvis/webhook``, o áudio passa a tocar no SessionFlow
(no aparelho) em vez de ir pro WhatsApp.

Recebe o payload, valida um token compartilhado (``X-Jarvis-Token`` vs
``settings.jarvis_token``) e republica um frame transiente ``jarvis_audio`` no
SSE — o mesmo que o {@link JarvisAudioService} do frontend toca.

Auth: este endpoint é ISENTO do middleware JWT (o hook do host não tem token de
usuário) e se protege pelo token próprio. Sem ``jarvis_token`` configurado, o
endpoint rejeita tudo (401) — desabilitado por padrão.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.publishers.event_publisher import publish_event

router = APIRouter(prefix="/jarvis", tags=["jarvis"])

# "label. resumo..." → captura o rótulo (nome da sessão/projeto) que o JARVIS
# prefixa no texto falado, para atribuir o áudio a uma sessão (só p/ exibição).
_LABEL_RE = re.compile(r"^([\w .-]{1,40}?)\.\s+(.*)$", re.DOTALL)


class JarvisWebhookIn(BaseModel):
    """Payload enviado pelo hook do JARVIS (campos do template do webhook)."""

    base64: str
    text: str = ""
    filename: str | None = None
    mime: str | None = None
    session: str | None = None


@router.post("/webhook", status_code=202)
async def jarvis_webhook(
    request: Request,
    body: JarvisWebhookIn,
    x_jarvis_token: str | None = Header(default=None),
) -> dict:
    """Recebe o áudio do JARVIS (away) e republica como frame ``jarvis_audio``."""
    settings = request.app.state.settings
    expected = settings.jarvis_token
    if not expected or x_jarvis_token != expected:
        raise HTTPException(status_code=401, detail="token inválido")
    if not body.base64:
        raise HTTPException(status_code=400, detail="áudio ausente")

    # Atribui à sessão: campo explícito, ou o rótulo prefixado no texto falado.
    session = body.session
    text = body.text or ""
    if not session and text:
        m = _LABEL_RE.match(text.strip())
        if m:
            session = m.group(1).strip()
    session = session or "jarvis"

    await publish_event(
        settings,
        {
            "type": "jarvis_audio",
            "session_id": session,
            "title": f"JARVIS — {session}",
            "text": text,
            "audio_b64": body.base64,
            "mime": body.mime or "audio/ogg",
            "at": datetime.now(UTC).isoformat(),
        },
    )
    return {"status": "queued", "session": session}
