"""Drives one WS connection's turn-taking (spec §7): buffers mic frames,
runs endpointing, and on utterance end calls STT and emits `transcript.final`.

Task 5 extends `_on_utterance_end` to route the text into a SessionFlow
session and stream the reply; kept here as a stub call for now.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable

from app.voice.agent_output_bridge import bridge_agent_output
from app.voice.session_router import route_global, route_session
from app.voice.speech_formatter import SentenceChunker, to_speech_text
from app.voice.vad import Endpointer

logger = logging.getLogger(__name__)


class TurnManager:
    def __init__(
        self,
        *,
        send_json: Callable[[dict], None],
        send_binary: Callable[[bytes], None],
        stt_client,
        tts_client,
        vad_is_speech: Callable[[bytes, int], bool],
        sessions_repo=None,
        silence_ms_to_end: int = 450,
        frame_ms: int = 20,
        events_broker=None,
        publish_command_fn=None,
    ) -> None:
        self._send_json = send_json
        self._send_binary = send_binary
        self._stt = stt_client
        self._tts = tts_client
        self._vad_is_speech = vad_is_speech
        self._sessions_repo = sessions_repo
        self._endpointer = Endpointer(silence_ms_to_end=silence_ms_to_end, frame_ms=frame_ms)
        self._utterance_buf = bytearray()
        self._turn_id: str | None = None
        self._cancelled_turns: set[str] = set()
        # Session mode (Task 7): "global" answers from the snapshot directly
        # (Task 5); "session" forwards text to a bound SessionFlow session
        # and streams its reply back via `_events_broker`.
        self._mode = "global"
        self._session_id: str | None = None
        self._host_id: str | None = None
        # ``tmux_name`` (não o Mongo ``_id`` em ``_session_id``) é a chave que
        # o resto do sistema usa: `_handle_input` do worker resolve a sessão
        # por `tmux_name`, e os eventos de output que voltam do worker levam
        # `session_id: tmux_name` (ver `output_capture.py::_publish`). Rotear
        # ou filtrar por `_session_id` (Mongo id) nunca bate com nada.
        self._tmux_name: str | None = None
        self._events_broker = events_broker
        self._publish_command = publish_command_fn
        # Session-mode turns run as background tasks (see `_on_utterance_end`)
        # so the WS receive loop in `routers/voice.py` stays free to read a
        # `voice.cancel` control message while `bridge_agent_output` is
        # pending on a `session_status` event that may never arrive (review
        # finding, task-7 fix round 1). `_turn_cancel_events` lets `cancel()`
        # wake a pending turn immediately instead of only marking it — the
        # in-flight `_run_session_turn` awaits it alongside the bridge task.
        self._turn_tasks: dict[str, asyncio.Task] = {}
        self._turn_cancel_events: dict[str, asyncio.Event] = {}
        # Per-turn latency instrumentation (spec §22) — see `_mark`/`_log_turn_metrics`.
        self._turn_timings: dict[str, dict[str, float]] = {}

    def _mark(self, turn_id: str, event: str) -> None:
        """Idempotent: only the FIRST call per (turn_id, event) records —
        repeat calls (e.g. each TTS chunk) don't overwrite the timestamp of
        the first chunk, which is what spec §22's "_first_" names ask for."""
        self._turn_timings.setdefault(turn_id, {}).setdefault(event, time.monotonic())

    def _log_turn_metrics(self, turn_id: str) -> None:
        t = self._turn_timings.get(turn_id)
        if not t or "speech_ended_at" not in t or "audio_first_sent_at" not in t:
            return  # incomplete turn (cancelled before any audio played) — nothing useful to report

        def dt(a: str, b: str) -> float | None:
            return round((t[b] - t[a]) * 1000, 1) if a in t and b in t else None

        metrics = {
            "turnId": turn_id,
            "endpointing_ms": dt("speech_started_at", "speech_ended_at"),
            "stt_ms": dt("speech_ended_at", "transcript_final_at"),
            "routing_ms": dt("transcript_final_at", "route_started_at"),
            "agent_ttft_ms": dt("route_started_at", "agent_first_token_at"),
            "tts_first_audio_ms": dt("agent_first_token_at", "tts_first_chunk_at"),
            "voice_first_response_ms": dt("speech_ended_at", "audio_first_sent_at"),
            "total_turn_ms": dt("speech_ended_at", "turn_completed_at"),
        }
        logger.info("voice.turn.metrics %s", metrics)

    async def bind_session(self, session_id: str) -> None:
        self._mode = "session"
        self._session_id = session_id
        self._host_id = None
        self._tmux_name = None
        if self._sessions_repo is not None:
            doc = await self._sessions_repo.get_session(session_id)
            if doc is not None:
                self._host_id = doc.get("host_id")
                self._tmux_name = doc.get("tmux_name")

    def unbind_session(self) -> None:
        self._mode = "global"
        self._session_id = None
        self._host_id = None
        self._tmux_name = None

    async def handle_frame(self, frame: bytes, *, index: int) -> None:
        is_speech = self._vad_is_speech(frame, index)
        if is_speech:
            self._utterance_buf.extend(frame)
        event = self._endpointer.feed(is_speech=is_speech)
        if event:
            # Diagnóstico temporário: só estado/índice, nunca conteúdo de áudio.
            logger.warning("voice.turn.vad event=%s index=%d speech=%s", event, index, is_speech)
        if event == "speech_start":
            old_turn_id = self._turn_id
            new_turn_id = str(uuid.uuid4())
            # Auto-cancel the previous turn (Task 8, bug 1): in session mode
            # it may still be running in `_turn_tasks` as a background task
            # when the next utterance starts — without this, two turns race
            # and can interleave audio. This also is the "bound queue" for
            # spec §18: at most one live session-mode turn at a time is a
            # semantic bound, not a new fixed-size buffer (YAGNI — no extra
            # queue abstraction needed on top of `_turn_tasks`).
            if (
                old_turn_id is not None
                and old_turn_id != new_turn_id
                and old_turn_id in self._turn_tasks
            ):
                await self.cancel(old_turn_id)
            self._turn_id = new_turn_id
            self._mark(new_turn_id, "speech_started_at")
            self._utterance_buf.clear()
            self._utterance_buf.extend(frame)
        elif event == "utterance_end":
            await self._on_utterance_end()

    async def _on_utterance_end(self) -> None:
        turn_id = self._turn_id
        pcm = bytes(self._utterance_buf)
        self._utterance_buf.clear()
        if turn_id is None or turn_id in self._cancelled_turns:
            self._cancelled_turns.discard(turn_id)
            return
        self._mark(turn_id, "speech_ended_at")
        text = await self._stt.transcribe(pcm)
        if turn_id in self._cancelled_turns:
            self._turn_timings.pop(turn_id, None)
            self._cancelled_turns.discard(turn_id)
            return  # cancelled while transcribing
        self._send_json({"type": "transcript.final", "turnId": turn_id, "text": text})
        self._mark(turn_id, "transcript_final_at")
        logger.warning(
            "voice.turn.transcribed turn=%s chars=%d mode=%s tmux_name=%s host_id=%s",
            turn_id, len(text), self._mode, self._tmux_name, self._host_id,
        )

        if self._mode == "session" and self._tmux_name:
            self._mark(turn_id, "route_started_at")
            # Fire-and-forget: `_run_session_turn` can block for a while on
            # `bridge_agent_output` (see class docstring note above). Awaiting
            # it here would block `handle_frame`, which blocks the WS receive
            # loop, which would make `voice.cancel` unreachable for this
            # turn — the exact bug fix round 1 addresses.
            self._turn_tasks[turn_id] = asyncio.create_task(
                self._run_session_turn(turn_id, text)
            )
            return

        # Global-mode turns must also run as a background task (Critical #5,
        # final review): running them inline blocked the WS receive loop for
        # the turn's full duration, making `voice.cancel` unreachable and
        # bypassing the auto-cancel guard in `handle_frame` (which only
        # checks `_turn_tasks`).
        self._mark(turn_id, "route_started_at")
        self._turn_tasks[turn_id] = asyncio.create_task(
            self._run_global_turn(turn_id, text)
        )

    async def _run_global_turn(self, turn_id: str, text: str) -> None:
        try:
            reply = await route_global(text, self._sessions_repo)
            self._mark(turn_id, "agent_first_token_at")
            if turn_id in self._cancelled_turns:
                self._turn_timings.pop(turn_id, None)
                return  # cancelled while routing
            self._send_json({"type": "assistant.text.delta", "turnId": turn_id, "text": reply})

            # This task's slice speaks the whole `route_global` reply at once,
            # chunked into sentence-shaped pieces (Task 7 makes this properly
            # incremental as the agent streams).
            chunker = SentenceChunker(max_chars=100)
            chunks = chunker.feed(reply)
            final_chunk = chunker.flush()
            if final_chunk:
                chunks.append(final_chunk)

            self._send_json(
                {
                    "type": "assistant.speech.start",
                    "turnId": turn_id,
                    "sampleRate": 22050,
                    "format": "pcm_s16le",
                }
            )
            for chunk in chunks:
                pcm, _sample_rate = await self._tts.synthesize(to_speech_text(chunk))
                self._mark(turn_id, "tts_first_chunk_at")
                if turn_id in self._cancelled_turns:
                    break  # cancelled while synthesizing this chunk
                self._send_binary(pcm)
                self._mark(turn_id, "audio_first_sent_at")
            if turn_id not in self._cancelled_turns:
                self._mark(turn_id, "turn_completed_at")
                self._log_turn_metrics(turn_id)
                self._send_json({"type": "assistant.speech.end", "turnId": turn_id})
            self._turn_timings.pop(turn_id, None)
        finally:
            self._turn_tasks.pop(turn_id, None)
            self._cancelled_turns.discard(turn_id)

    async def _run_session_turn(self, turn_id: str, text: str) -> None:
        """Session mode (Task 7): fire `route_session`, then speak the reply
        AS IT STREAMS via `agent_output_bridge` — each output line becomes a
        chunker `feed()` call, so the first sentence is synthesized without
        waiting for the rest of the reply to arrive.

        Runs as its own task (spawned by `_on_utterance_end`), and races the
        bridge against `cancel_event` so `cancel()` can interrupt a bridge
        stuck waiting for a `session_status` event that never arrives (fix
        round 1 — nothing publishes that event yet, see task-7 report).
        """
        cancel_event = asyncio.Event()
        self._turn_cancel_events[turn_id] = cancel_event
        try:
            chunker = SentenceChunker(max_chars=100)
            self._send_json(
                {
                    "type": "assistant.speech.start",
                    "turnId": turn_id,
                    "sampleRate": 22050,
                    "format": "pcm_s16le",
                }
            )

            async def on_delta(delta_text: str) -> None:
                self._mark(turn_id, "agent_first_token_at")
                if turn_id in self._cancelled_turns:
                    return
                for chunk in chunker.feed(delta_text):
                    pcm, _sample_rate = await self._tts.synthesize(to_speech_text(chunk))
                    self._mark(turn_id, "tts_first_chunk_at")
                    if turn_id in self._cancelled_turns:
                        return
                    self._send_binary(pcm)
                    self._mark(turn_id, "audio_first_sent_at")

            async def on_done() -> None:
                if turn_id not in self._cancelled_turns:
                    final_chunk = chunker.flush()
                    if final_chunk:
                        pcm, _sample_rate = await self._tts.synthesize(to_speech_text(final_chunk))
                        self._mark(turn_id, "tts_first_chunk_at")
                        if turn_id not in self._cancelled_turns:
                            self._send_binary(pcm)
                            self._mark(turn_id, "audio_first_sent_at")
                if turn_id not in self._cancelled_turns:
                    self._mark(turn_id, "turn_completed_at")
                    self._log_turn_metrics(turn_id)
                    self._send_json({"type": "assistant.speech.end", "turnId": turn_id})

            self._mark(turn_id, "route_started_at")
            await route_session(text, self._tmux_name, self._publish_command, self._host_id)
            if turn_id in self._cancelled_turns:
                return

            bridge_task = asyncio.create_task(
                bridge_agent_output(
                    self._tmux_name, self._events_broker, on_delta, on_done, echo_text=text
                )
            )
            cancel_wait = asyncio.create_task(cancel_event.wait())
            try:
                # 60s safeguard: no code publishes a terminal `session_status`
                # event yet (task-7 report concern #1) — without a bound, a
                # session that never reaches one would leak this task forever
                # even without an explicit cancel.
                done, _pending = await asyncio.wait(
                    {bridge_task, cancel_wait},
                    timeout=60,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if bridge_task in done:
                    bridge_task.result()  # surface any exception
                else:
                    bridge_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await bridge_task
                    if turn_id not in self._cancelled_turns:
                        # 60s safeguard fired, not a real cancel — still
                        # close the turn cleanly for the UI.
                        self._mark(turn_id, "turn_completed_at")
                        self._log_turn_metrics(turn_id)
                        self._send_json({"type": "assistant.speech.end", "turnId": turn_id})
                    # else: real cancellation — no closing frame, no more
                    # audio, matching the "suppress everything" guarantee
                    # `cancel()` already gives the global-mode path.
            finally:
                cancel_wait.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_wait
        finally:
            self._turn_cancel_events.pop(turn_id, None)
            self._turn_tasks.pop(turn_id, None)
            self._turn_timings.pop(turn_id, None)
            self._cancelled_turns.discard(turn_id)

    async def cancel(self, turn_id: str) -> None:
        self._cancelled_turns.add(turn_id)
        event = self._turn_cancel_events.get(turn_id)
        if event is not None:
            event.set()

    async def cancel_all(self) -> None:
        """Spec §21 (disconnect must cancel the current turn): cancels every
        turn still in flight, e.g. when the WS drops while a session-mode
        turn is running as a background task (`_turn_tasks`) — without this
        it keeps running orphaned, trying to `send_json`/`send_binary` on a
        closed socket."""
        turn_ids = list(self._turn_tasks.keys())
        for turn_id in turn_ids:
            await self.cancel(turn_id)
        tasks = [self._turn_tasks[t] for t in turn_ids if t in self._turn_tasks]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
