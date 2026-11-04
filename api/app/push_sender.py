"""Envio de Web Push (VAPID) pela API — usado por ``POST /push/notify``.

Espelho do ``worker/sessionflow_worker/push_sender.py`` (a imagem da API não
carrega o pacote do worker): mesmo payload ``{"notification": {...}}`` que o
Service Worker do Angular (ngsw) entende, mesma poda de subscrições 404/410.
A chave privada vem de ``settings.vapid_private`` (``SESSIONFLOW_VAPID_PRIVATE``
do mesmo ``.env``; ``\\n`` escapado). ``webpush`` é bloqueante → executor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("sessionflow_api.push_sender")

_ICON = "icons/icon-192x192-v2.png"
_BADGE = "icons/badge-96.png"

def private_pem(raw: str) -> str:
    return (raw or "").replace("\\n", "\n")

@lru_cache(maxsize=4)
def _vapid(pem: str) -> Any:
    # O pywebpush não aceita a PEM crua como string — aceita um Vapid instance.
    from py_vapid import Vapid01

    return Vapid01.from_pem(pem.encode())

def build_payload(title: str, body: str, url: str | None = None) -> str:
    notification: dict[str, Any] = {
        "title": title,
        "body": body,
        "icon": _ICON,
        "badge": _BADGE,
        "tag": url or title,
        "vibrate": [300, 120, 300],
    }
    if url:
        notification["data"] = {
            "onActionClick": {
                "default": {"operation": "navigateLastFocusedOrOpen", "url": url}
            }
        }
    return json.dumps({"notification": notification})

def _send_one_sync(sub: dict[str, Any], payload: str, pem: str, subject: str) -> int | None:
    """Envia um push (bloqueante). Retorna o status HTTP de erro p/ poda, ou None."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=payload,
            vapid_private_key=_vapid(pem),
            vapid_claims={"sub": subject},
            timeout=10,
        )
        return None
    except WebPushException as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status if isinstance(status, int) else 0

async def send_to_all(
    db: AsyncIOMotorDatabase,
    collection: str,
    pem: str,
    subject: str,
    title: str,
    body: str,
    url: str | None = None,
) -> int:
    """Envia ``{title, body}`` a TODAS as subscrições. Retorna quantas enviou."""
    subs = await db[collection].find({}).to_list(length=1000)
    if not subs:
        return 0
    payload = build_payload(title, body, url)
    loop = asyncio.get_running_loop()
    sent = 0
    for sub in subs:
        status = await loop.run_in_executor(None, _send_one_sync, sub, payload, pem, subject)
        if status in (404, 410):
            await db[collection].delete_one({"endpoint": sub["endpoint"]})
            logger.info("push: subscrição expirada removida (%s)", status)
        elif status is None:
            sent += 1
    return sent
