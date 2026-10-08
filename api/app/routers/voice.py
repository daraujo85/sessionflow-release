"""Voice conversation gateway: WS connection + ticket issuance.

Etapa 2 of the Voice MVP (spec §28): the WebSocket buffers mic frames through
`TurnManager`, which runs VAD-based endpointing and emits `transcript.final`
via STT once an utterance ends. Later tasks add routing/TTS on top.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import webrtcvad
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from app.publishers.command_publisher import publish_command
from app.repositories.sessions_repo import SessionsRepository
from app.voice.stt_client import SttClient
from app.voice.ticket import consume_ticket, new_ticket
from app.voice.tts_client import TtsClient
from app.voice.turn_manager import TurnManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])


@router.post("/api/voice/ticket")
async def issue_voice_ticket(request: Request) -> dict:
    user = request.state.user  # set by the existing auth middleware (app.main)
    ticket = new_ticket(request.app.state.voice_tickets, user_id=user["id"])
    return {"ticket": ticket, "ttl_seconds": 45}


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    ticket = websocket.query_params.get("ticket")
    user_id = consume_ticket(websocket.app.state.voice_tickets, ticket)
    if user_id is None:
        await websocket.close(code=4401)  # policy violation, custom code
        return
    await websocket.accept()
    vad = webrtcvad.Vad(2)  # aggressiveness 0-3
    settings = websocket.app.state.settings
    stt = SttClient(settings.voice_stt_url)
    tts = TtsClient(settings.voice_tts_url)
    sessions_repo = SessionsRepository(
        websocket.app.state.mongo_db, settings.sessions_collection
    )

    # Single outbound queue + sender task: `TurnManager.send_json`/
    # `send_binary` are plain (sync) callables (see its unit test) that must
    # never race each other on the same WebSocket (Starlette forbids
    # concurrent send_* calls) — queuing here guarantees one send in flight
    # at a time, in submission order (Important #9, final review).
    # ponytail: unbounded — bounds on `TurnManager`'s own backpressure (spec
    # §18) are handled at `EventsBroker` (drop-oldest, see Important #11);
    # this queue only serializes ordering. Add a max size + policy if a slow
    # client is ever observed to grow this unbounded (YAGNI until measured).
    out_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    async def _sender() -> None:
        while True:
            kind, payload = await out_queue.get()
            try:
                if kind == "json":
                    await websocket.send_json(payload)
                else:
                    await websocket.send_bytes(payload)  # type: ignore[arg-type]
            except Exception:
                logger.debug("voice_ws: send failed (socket likely closing)", exc_info=True)

    sender_task = asyncio.create_task(_sender())

    tm = TurnManager(
        send_json=lambda msg: out_queue.put_nowait(("json", msg)),
        send_binary=lambda b: out_queue.put_nowait(("bytes", b)),
        stt_client=stt,
        tts_client=tts,
        vad_is_speech=lambda frame, _i: vad.is_speech(frame, 16000),
        sessions_repo=sessions_repo,
        events_broker=getattr(websocket.app.state, "events_broker", None),
        # A plain (sync) callable that returns the `publish_command`
        # coroutine — `route_session` awaits it. Settings are bound here so
        # `TurnManager`/`route_session` never need to know about `Settings`.
        publish_command_fn=lambda **kwargs: publish_command(settings, **kwargs),
    )
    index = 0
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(msg.get("code", 1000))
            if msg.get("text") is not None:
                control = json.loads(msg["text"])
                control_type = control.get("type")
                if control_type == "voice.bind_session":
                    await tm.bind_session(control["sessionId"])
                    logger.warning("voice_ws: bound session=%s; awaiting PCM frames", control["sessionId"])
                elif control_type == "voice.cancel":
                    await tm.cancel(control["turnId"])
                elif control_type == "ping":
                    continue
                continue
            frame = msg["bytes"]
            # Diagnóstico temporário: confirma chegada de PCM sem logar conteúdo.
            # 20 ms @ 16 kHz / PCM16 = 640 bytes por frame.
            if index == 0 or index % 100 == 0:
                logger.warning("voice_ws: received frame index=%d bytes=%d", index, len(frame))
            await tm.handle_frame(frame, index=index)
            index += 1
    except WebSocketDisconnect:
        # spec §21: on disconnect, cancel the current turn — otherwise a
        # session-mode turn still running in `tm._turn_tasks` keeps going
        # orphaned, trying to send on an already-closed socket.
        await tm.cancel_all()
    except Exception:
        # Any other failure (malformed frame, STT/TTS error propagating out
        # of a foreground global-mode turn, bad control JSON) must still run
        # the same cleanup — otherwise a session-mode background turn leaks
        # forever (Critical #4, final review).
        logger.exception("voice_ws: unhandled error, cancelling turns")
        await tm.cancel_all()
        raise
    finally:
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task
