"""Envio de Web Push (VAPID) — notificação com o app fechado.

Lê as subscrições do navegador (coleção ``push_subscriptions``, gravada pela
API) e envia uma notificação a cada uma, assinando com a chave privada VAPID.
Subscrições mortas (404/410) são removidas. O ``webpush`` é síncrono/bloqueante,
então rodamos em ``run_in_executor`` para não travar o event loop do worker.

Payload no formato que o Service Worker do Angular (ngsw) entende:
``{"notification": {title, body, icon, badge, data:{onActionClick}}}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("sessionflow_worker.push_sender")

PUSH_SUBSCRIPTIONS_COLLECTION = "push_subscriptions"

_ICON = "icons/icon-192x192-v2.png"
# Badge = ícone monocromático (silhueta branca/transparente) p/ a barra de
# status do Android — o ícone cheio virava quadrado branco (SO usa só o alfa).
_BADGE = "icons/badge-96.png"


def _vapid_private_pem() -> str:
    """Chave privada VAPID do ambiente (``\\n`` vem escapado no .env). Lazy:
    lida na hora do envio (após o load_dotenv), não no import do módulo."""
    return (os.environ.get("SESSIONFLOW_VAPID_PRIVATE", "") or "").replace("\\n", "\n")


def _vapid_subject() -> str:
    return os.environ.get("SESSIONFLOW_VAPID_SUBJECT", "mailto:admin@example.com")


# Cache do objeto Vapid (o pywebpush não aceita a PEM como string crua — aceita
# um Vapid instance, caminho de arquivo, ou base64 DER). Construímos da PEM.
_vapid_obj: Any = None


def _vapid() -> Any:
    global _vapid_obj
    if _vapid_obj is None:
        from py_vapid import Vapid01

        _vapid_obj = Vapid01.from_pem(_vapid_private_pem().encode())
    return _vapid_obj


def _send_one_sync(sub: dict[str, Any], payload: str) -> int | None:
    """Envia um push (bloqueante). Retorna o status HTTP de erro p/ poda, ou None."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=payload,
            vapid_private_key=_vapid(),
            vapid_claims={"sub": _vapid_subject()},
            timeout=10,
        )
        return None
    except WebPushException as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status if isinstance(status, int) else 0


async def send_to_all(
    db: AsyncIOMotorDatabase,
    title: str,
    body: str,
    url: str | None = None,
    collection: str = PUSH_SUBSCRIPTIONS_COLLECTION,
) -> int:
    """Envia ``{title, body}`` a TODAS as subscrições. Retorna quantas enviou.

    Best-effort: sem chave privada configurada, é no-op. Subscrições com 404/410
    (expiradas) são removidas. Cada envio roda no executor (bloqueante).
    """
    if not _vapid_private_pem():
        return 0
    subs = await db[collection].find({}).to_list(length=1000)
    if not subs:
        return 0

    notification: dict[str, Any] = {
        "title": title,
        "body": body,
        "icon": _ICON,
        "badge": _BADGE,
        "tag": url or title,
        # Padrão de vibração (ms): buzz-pausa-buzz. Honrado pelo Android quando a
        # notificação aparece com o app fechado/em segundo plano; iOS ignora.
        "vibrate": [300, 120, 300],
    }
    if url:
        notification["data"] = {
            "onActionClick": {
                "default": {"operation": "navigateLastFocusedOrOpen", "url": url}
            }
        }
    payload = json.dumps({"notification": notification})

    loop = asyncio.get_event_loop()
    sent = 0
    for sub in subs:
        status = await loop.run_in_executor(None, _send_one_sync, sub, payload)
        if status in (404, 410):
            await db[collection].delete_one({"endpoint": sub["endpoint"]})
            logger.info("push: subscrição expirada removida (%s)", status)
        elif status is None:
            sent += 1
    return sent
