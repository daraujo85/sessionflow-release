"""Read endpoints: Worker status (`GET /worker`) e limites de uso (`GET /usage`).

O Worker (no host) faz heartbeat em ``worker_status`` (hostname/started_at/
updated_at) e raspa o ``/usage`` do Claude em ``host_usage``. Estes endpoints
expõem esses dados REAIS para o Perfil — nada é fabricado.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["worker"])

WORKER_STATUS_COLLECTION = "worker_status"
HOST_USAGE_COLLECTION = "host_usage"
HOST_USAGE_KEY = "host"
# updated_at mais velho que isto ⇒ worker considerado offline.
ONLINE_WINDOW_SECONDS = 30.0


class WorkerOut(BaseModel):
    """Status do Worker para o card do Perfil."""

    online: bool = False
    hostname: str | None = None
    uptime_seconds: float | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None


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


@router.get("/worker", response_model=WorkerOut)
async def get_worker(request: Request) -> WorkerOut:
    db = request.app.state.mongo_db
    doc = await db[WORKER_STATUS_COLLECTION].find_one({"_id": "worker"})
    if not doc:
        return WorkerOut(online=False)
    now = datetime.now(timezone.utc)
    updated = _aware(doc.get("updated_at"))
    started = _aware(doc.get("started_at"))
    online = updated is not None and (now - updated).total_seconds() < ONLINE_WINDOW_SECONDS
    uptime = (now - started).total_seconds() if (online and started) else None
    return WorkerOut(
        online=online,
        hostname=doc.get("hostname"),
        uptime_seconds=uptime,
        started_at=started,
        updated_at=updated,
    )


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
