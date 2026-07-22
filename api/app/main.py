"""SessionFlow API — FastAPI application scaffold.

Provides app creation with CORS, Mongo (motor) and RabbitMQ (aio-pika)
clients managed via the lifespan, and a ``GET /health`` endpoint that pings
Mongo.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Any

import aio_pika
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient

from app import share
from app.auth import decode_token, extract_token
from app.config import Settings, get_settings
from app.events_broker import EventsBroker
from app.repositories.sessions_repo import SessionsRepository
from app.routers import auth as auth_router
from app.routers import directories as directories_router
from app.routers import events as events_router
from app.routers import history as history_router
from app.routers import jarvis as jarvis_router
from app.routers import models as models_router
from app.routers import outputs as outputs_router
from app.routers import profile as profile_router
from app.routers import push as push_router
from app.routers import schedules as schedules_router
from app.routers import screen as screen_router
from app.routers import sessions as sessions_router
from app.routers import settings as settings_router
from app.routers import shared_files as shared_files_router
from app.routers import worker as worker_router
from app.scheduler import run_scheduler_forever


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(settings.effective_mongo_uri)
    app.state.mongo_client = mongo_client
    app.state.mongo_db = mongo_client[settings.mongo_db]

    rabbit_connection: aio_pika.abc.AbstractRobustConnection | None = None
    try:
        rabbit_connection = await aio_pika.connect_robust(settings.effective_rabbitmq_uri)
    except Exception:
        # Rabbit is optional for the health scaffold; don't crash startup.
        rabbit_connection = None
    app.state.rabbit_connection = rabbit_connection

    # SSE fan-out broker (DASH-01). Never crash startup if Rabbit is down.
    events_broker = EventsBroker(settings.effective_rabbitmq_uri)
    app.state.events_broker = events_broker
    await events_broker.start()

    # Comandos programados: loop solto que dispara os vencidos (ver
    # app/scheduler.py). Cancelado no shutdown como qualquer outra task.
    scheduler_task = asyncio.create_task(
        run_scheduler_forever(app.state.mongo_db, settings)
    )

    try:
        yield
    finally:
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task
        await events_broker.stop()
        if rabbit_connection is not None:
            await rabbit_connection.close()
        mongo_client.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(title="SessionFlow API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    # NOTE: middleware order — Starlette runs the LAST-added middleware
    # OUTERMOST. We add CORS last so it wraps the auth middleware: that way
    # the CORS preflight (OPTIONS) is handled before auth, and CORS headers
    # are attached even to 401 responses from the auth layer.
    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        method = request.method
        path = request.url.path
        cfg = request.app.state.settings
        # Auth is "configured" only when an account email is set. If it is not
        # (e.g. unrelated integration tests, or a fresh deploy before the env is
        # populated) the middleware is a no-op. In production SESSIONFLOW_EMAIL
        # is set, so every route below is protected.
        if not cfg.auth_email:
            return await call_next(request)
        # Exemptions: CORS preflight, the health probe, and the auth endpoints
        # (login + public webauthn). Everything else needs a valid JWT.
        if (
            method == "OPTIONS"
            or (method == "GET" and path in ("/health", "/version"))
            or path.startswith("/auth/")
            # Webhook inbound do JARVIS (host → API): protegido por token próprio
            # (X-Jarvis-Token), não pelo JWT de usuário.
            or path == "/jarvis/webhook"
        ):
            return await call_next(request)

        token = extract_token(request)
        claims = decode_token(token or "", secret=cfg.jwt_secret)
        if claims is not None:
            return await call_next(request)

        # Sem JWT de usuário → tenta TOKEN DE SHARE (link compartilhável de 1
        # sessão). Ele só é aceito nas rotas DAQUELA session_id (e no SSE
        # filtrado por ela), nunca no resto da API — e enquanto a sessão estiver
        # viva/no prazo (checado contra o banco a cada request).
        share_tok = request.query_params.get("k") or request.headers.get("x-share-token")
        if share_tok:
            target = share.session_id_from_path(path)
            if target is None and path == "/events":
                target = request.query_params.get("session")
            path_ok = bool(target) and (
                share.path_allows_share(path, target) or path == "/events"
            )
            if target and path_ok:
                repo = SessionsRepository(
                    request.app.state.mongo_db, cfg.sessions_collection
                )
                doc = await repo.get_session(target)
                if share.token_valid(doc, share_tok):
                    request.state.share_session_id = target
                    return await call_next(request)
            return JSONResponse(status_code=403, content={"detail": "forbidden"})

        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        mongo_ok = False
        try:
            await app.state.mongo_db.command("ping")
            mongo_ok = True
        except Exception:
            mongo_ok = False
        return {"status": "ok", "mongo": mongo_ok}

    @app.get("/version")
    async def version() -> dict[str, str]:
        s = app.state.settings
        return {"version": s.release_version, "git_sha": s.git_sha}

    app.include_router(auth_router.router)
    app.include_router(sessions_router.router)
    app.include_router(directories_router.router)
    app.include_router(models_router.router)
    app.include_router(outputs_router.router)
    app.include_router(screen_router.router)
    app.include_router(history_router.router)
    app.include_router(events_router.router)
    app.include_router(worker_router.router)
    app.include_router(profile_router.router)
    app.include_router(push_router.router)
    app.include_router(settings_router.router)
    app.include_router(jarvis_router.router)
    app.include_router(schedules_router.router)
    app.include_router(shared_files_router.router)

    return app


app = create_app()
