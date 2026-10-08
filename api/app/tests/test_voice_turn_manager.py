import asyncio
import itertools
import logging
from types import SimpleNamespace

import pytest

from app.voice.turn_manager import TurnManager


def _patch_monotonic(monkeypatch, *, start: float = 0.0, step: float = 0.1) -> None:
    """Deterministic increasing clock for `_mark` (spec §22 instrumentation).
    Patches only `app.voice.turn_manager.time` (not the global stdlib `time`
    module) so asyncio's own internal clock — which imports `time` itself —
    is unaffected."""
    counter = itertools.count(start, step)
    monkeypatch.setattr(
        "app.voice.turn_manager.time", SimpleNamespace(monotonic=lambda: next(counter))
    )


class FakeSttClient:
    async def transcribe(self, pcm: bytes) -> str:
        return "quais sessões estão ativas"


class FakeVad:
    """Every frame after the 3rd is silence; ends the utterance on frame 23."""

    def is_speech(self, frame: bytes, index: int) -> bool:
        return index < 3


class FakeSessionsRepo:
    async def list_active(self):
        return [{"name": "pagamentos"}]

    # Mirrors the real Mongo doc shape (`tmux_name` distinct from the `_id`
    # passed as `session_id`) — here they're the same string since tests
    # only care that routing/bridge use *some* consistent name.
    async def get_session(self, session_id: str):
        return {"tmux_name": session_id}


class FakeTtsClient:
    async def synthesize(self, text: str) -> tuple[bytes, int]:
        return b"\x00\x01", 22050


@pytest.mark.asyncio
async def test_utterance_end_emits_transcript_final_only():
    sent = []
    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    if tm._turn_tasks:
        await asyncio.gather(*tm._turn_tasks.values())
    finals = [m for m in sent if m["type"] == "transcript.final"]
    partials = [m for m in sent if m["type"] == "transcript.partial"]
    deltas = [m for m in sent if m["type"] == "assistant.text.delta"]
    assert len(finals) == 1
    assert finals[0]["text"] == "quais sessões estão ativas"
    assert partials == []  # MVP: final-only (see plan note)
    assert len(deltas) == 1
    assert "pagamentos" in deltas[0]["text"]


@pytest.mark.asyncio
async def test_cancel_during_route_global_suppresses_delta():
    """A turn cancelled while `route_global`'s Mongo read is in flight must
    not emit `assistant.text.delta` — the turnId-invalidation guarantee
    (Global Constraint) applies across that await too, not just the STT one."""
    sent = []
    reached_list_active = asyncio.Event()
    resume = asyncio.Event()

    class SlowSessionsRepo:
        async def list_active(self):
            reached_list_active.set()
            await resume.wait()
            return []

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=SlowSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )

    async def feed():
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)

    task = asyncio.create_task(feed())
    await reached_list_active.wait()
    turn_id = tm._turn_id
    await tm.cancel(turn_id)
    resume.set()
    await task

    finals = [m for m in sent if m["type"] == "transcript.final"]
    deltas = [m for m in sent if m["type"] == "assistant.text.delta"]
    assert len(finals) == 1  # already sent before the cancel took effect
    assert deltas == []


class FakeSessionBroker:
    """Broker whose queue the test drives directly (unlike a pre-filled
    fixture) — lets us prove events are consumed as they arrive, not
    buffered until the whole reply exists."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.unsubscribed = False

    async def subscribe(self):
        return self.queue

    async def unsubscribe(self, queue) -> None:
        self.unsubscribed = True


@pytest.mark.asyncio
async def test_session_mode_speaks_first_sentence_before_reply_completes():
    """Decisive acceptance test (task-7 brief): the first
    `assistant.speech` chunk must be synthesized as soon as the first output
    line arrives — before the rest of the reply exists — not after the full
    text has accumulated."""
    sent = []
    tts_calls = []

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            tts_calls.append(text)
            return b"\x00\x01", 22050

    broker = FakeSessionBroker()
    published = []

    async def fake_publish_command_fn(**kwargs):
        published.append(kwargs)
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    async def feed():
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)

    # `handle_frame` no longer blocks on the session turn (fix round 1: it
    # runs as its own background task so the WS receive loop stays free for
    # `voice.cancel`) — the frame-feeding loop itself finishes fast.
    await asyncio.create_task(feed())
    turn_id = tm._turn_id
    # Let route_session fire and the bridge subscribe/block on the queue.
    for _ in range(5):
        await asyncio.sleep(0)
    assert published == [
        {
            "type": "input",
            "payload": {"name": "sess-1", "text": "quais sessões estão ativas"},
            "host_id": None,
        }
    ]

    await broker.queue.put(
        {"session": "sess-1", "seq": 1, "text": "Encontrei o problema.", "line_type": "agent"}
    )
    for _ in range(5):
        await asyncio.sleep(0)
    # First sentence spoken while the "rest of the reply" hasn't been sent yet.
    assert tts_calls == ["Encontrei o problema."]

    await broker.queue.put(
        {"session": "sess-1", "seq": 2, "text": " Segue a explicação completa.", "line_type": "agent"}
    )
    await broker.queue.put(
        {"type": "session_status", "session": "sess-1", "status": "waiting_input"}
    )
    await tm._turn_tasks[turn_id]

    assert tts_calls == ["Encontrei o problema.", "Segue a explicação completa."]
    assert sent[-1] == {"type": "assistant.speech.end", "turnId": turn_id}


@pytest.mark.asyncio
async def test_bind_session_resolves_host_id_and_forwards_it_to_publish():
    """Critical #2: `sessionflow` is a DIRECT exchange — each worker binds
    only `sessionflow.commands.<host_id>`. Without forwarding `host_id`,
    `publish_command` routes to the unconsumed legacy key and the `input`
    command is silently dropped."""
    sent = []
    published = []

    class FakeSessionsRepoWithHost:
        async def get_session(self, session_id: str):
            return {"host_id": "host-1", "tmux_name": session_id}

    async def fake_publish_command_fn(**kwargs):
        published.append(kwargs)
        return "cmd-1"

    broker = FakeSessionBroker()
    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepoWithHost(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")
    assert tm._host_id == "host-1"

    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    for _ in range(5):
        await asyncio.sleep(0)

    assert published == [
        {
            "type": "input",
            "payload": {"name": "sess-1", "text": "quais sessões estão ativas"},
            "host_id": "host-1",
        }
    ]


@pytest.mark.asyncio
async def test_cancel_during_session_turn_stops_audio_and_does_not_hang():
    """Review finding (task-7 fix round 1): the receive loop must be able to
    reach `voice.cancel` while a session turn's bridge is still pending on a
    `session_status` event that never arrives — and once cancelled, no more
    TTS/audio may be emitted, and the background turn task must actually
    finish (not hang forever)."""
    sent = []
    tts_calls = []

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            tts_calls.append(text)
            return b"\x00\x01", 22050

    broker = FakeSessionBroker()

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    async def feed():
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)

    await asyncio.create_task(feed())
    turn_id = tm._turn_id
    for _ in range(5):
        await asyncio.sleep(0)

    await broker.queue.put(
        {"session": "sess-1", "seq": 1, "text": "Encontrei o problema.", "line_type": "agent"}
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert tts_calls == ["Encontrei o problema."]  # first sentence already spoken

    # Cancel while the bridge is still blocked on queue.get() (no terminal
    # `session_status` was ever pushed) — this is the deadlock scenario the
    # review finding described.
    await asyncio.wait_for(tm.cancel(turn_id), timeout=1)

    # The background turn task must finish promptly instead of hanging.
    task = tm._turn_tasks.get(turn_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=2)

    # A second output line arriving after cancellation must NOT be spoken.
    await broker.queue.put(
        {"session": "sess-1", "seq": 2, "text": " Segue a explicação completa.", "line_type": "agent"}
    )
    for _ in range(5):
        await asyncio.sleep(0)

    assert tts_calls == ["Encontrei o problema."]  # no additional audio synthesized
    assert turn_id not in tm._turn_tasks  # task cleaned up, not leaked/hanging


@pytest.mark.asyncio
async def test_new_turn_cancels_previous_inflight_session_turn():
    """Bug 1: a new `speech_start` while the previous session-mode turn is
    still running (blocked on the broker queue, never resolving on its own)
    must auto-cancel that previous turn — otherwise both run concurrently
    and can interleave audio to the client."""
    sent = []
    tts_calls = []

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            tts_calls.append(text)
            return b"\x00\x01", 22050

    broker = FakeSessionBroker()

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: tts_calls.append("BINARY"),
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    # Turn A: frames that fire speech_start + utterance_end.
    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    turn_a = tm._turn_id
    for _ in range(5):
        await asyncio.sleep(0)
    assert turn_a in tm._turn_tasks  # A is running in the background, blocked on the queue

    binaries_after_a_started = len(tts_calls)

    # Turn B: a new speech_start + utterance_end before A ever finishes.
    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    turn_b = tm._turn_id
    assert turn_b != turn_a
    for _ in range(5):
        await asyncio.sleep(0)

    # Important #10: `_cancelled_turns` is discarded once A's background task
    # fully unwinds — no permanent leak of cancelled turn ids.
    assert turn_a not in tm._cancelled_turns
    assert turn_a not in tm._turn_tasks  # cleaned up, not leaked
    assert len(tts_calls) == binaries_after_a_started  # nothing new spoken for A


@pytest.mark.asyncio
async def test_global_mode_cancel_suppresses_speech_end():
    """Bug 2: cancelling mid-chunk in global mode must suppress
    `assistant.speech.end` too — matching session mode's documented
    "no closing frame after a real cancel" guarantee."""
    sent = []
    reached_synth = asyncio.Event()
    resume = asyncio.Event()

    class SlowTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            reached_synth.set()
            await resume.wait()
            return b"\x00\x01", 22050

    class MultiSentenceSttClient:
        async def transcribe(self, pcm: bytes) -> str:
            return "quais sessões estão ativas"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=MultiSentenceSttClient(),
        tts_client=SlowTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    tm._mode = "global"  # exercise the global-reply branch directly (route_global still needs sessions_repo)

    async def feed():
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)

    task = asyncio.create_task(feed())
    await reached_synth.wait()
    turn_id = tm._turn_id
    await tm.cancel(turn_id)
    resume.set()
    await task

    ends = [m for m in sent if m["type"] == "assistant.speech.end" and m["turnId"] == turn_id]
    assert ends == []


@pytest.mark.asyncio
async def test_cancel_races_exactly_with_bridge_completion():
    """Task 5 bug class, replayed for the session-mode bridge race: when
    `bridge_agent_output`'s underlying event is already available (no
    blocking await) at the same moment `cancel()` fires, `asyncio.wait`
    can resolve both `bridge_task` and `cancel_wait` in the same tick.
    Must not crash and must not leak `_turn_tasks`/`_turn_cancel_events`."""
    sent = []
    tts_calls = []

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            tts_calls.append(text)
            return b"\x00\x01", 22050

    broker = FakeSessionBroker()
    # Pre-fill the queue so the bridge's next `queue.get()` resolves without
    # a blocking await — ready to race against `cancel_event.wait()`.
    await broker.queue.put(
        {"type": "session_status", "session": "sess-1", "status": "waiting_input"}
    )

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    turn_id = tm._turn_id

    # Let the background task reach `asyncio.wait({bridge_task, cancel_wait})`
    # — bridge_task's next `queue.get()` resolves immediately (item already
    # queued) rather than blocking, so it's ready to complete on the same
    # tick `cancel_event.set()` wakes `cancel_wait`.
    for _ in range(3):
        await asyncio.sleep(0)
    await tm.cancel(turn_id)

    task = tm._turn_tasks.get(turn_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=2)

    assert turn_id not in tm._turn_tasks
    assert turn_id not in tm._turn_cancel_events


@pytest.mark.asyncio
async def test_session_mode_on_done_suppresses_speech_end_after_cancel():
    """Same bug class as Bug 2 (global mode), inside `on_done()` in
    `_run_session_turn`: when the bridge finishes normally (terminal
    `session_status`) right after `cancel()` was already called, `on_done`
    must not send `assistant.speech.end` for the already-cancelled turn.

    Reproduced deterministically: `cancel()` has no internal `await`, so
    calling it from inside the fake queue's `get()` runs synchronously —
    `bridge_agent_output` reaches `on_done` with the turn already cancelled,
    all before the racing `cancel_wait` task in `_run_session_turn` ever
    gets scheduled to notice the cancellation itself.
    """
    sent = []
    turn_box: dict = {}

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            return b"\x00\x01", 22050

    class CancelingQueue:
        def __init__(self) -> None:
            self._served = False

        async def get(self):
            if not self._served:
                self._served = True
                await tm.cancel(turn_box["turn_id"])
                return {
                    "type": "session_status",
                    "session": "sess-1",
                    "status": "waiting_input",
                }
            await asyncio.Event().wait()  # never called again in this test

    class CancelingBroker:
        async def subscribe(self):
            return CancelingQueue()

        async def unsubscribe(self, queue) -> None:
            pass

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=CancelingBroker(),
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    turn_id = tm._turn_id
    turn_box["turn_id"] = turn_id

    task = tm._turn_tasks.get(turn_id)
    assert task is not None
    await asyncio.wait_for(task, timeout=2)

    # Important #10: discarded from `_cancelled_turns` once the task's
    # `finally` runs — no permanent leak.
    assert turn_id not in tm._cancelled_turns
    ends = [m for m in sent if m["type"] == "assistant.speech.end" and m["turnId"] == turn_id]
    assert ends == []


@pytest.mark.asyncio
async def test_double_cancel_is_idempotent():
    """Calling `cancel()` twice for the same turnId (already finished or
    never existed) must not raise."""
    tm = TurnManager(
        send_json=lambda msg: None,
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    turn_id = "turn-never-existed"
    await tm.cancel(turn_id)
    await tm.cancel(turn_id)
    assert turn_id in tm._cancelled_turns


@pytest.mark.asyncio
async def test_cancel_all_cancels_every_inflight_session_turn():
    """Spec §21 / server-side WS-disconnect fix: `cancel_all()` must cancel
    every turn still running in `_turn_tasks` (not just one), so the router
    can call it from the `WebSocketDisconnect` handler and guarantee no
    orphaned session-mode turn keeps running after the socket closed."""
    sent = []

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            return b"\x00\x01", 22050

    broker_a = FakeSessionBroker()
    broker_b = FakeSessionBroker()

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker_a,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    # Turn A: fires speech_start + utterance_end, blocks in the background
    # on broker_a's queue.get() (never resolves on its own).
    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    turn_a = tm._turn_id
    for _ in range(5):
        await asyncio.sleep(0)
    assert turn_a in tm._turn_tasks

    # Swap in a second broker and fire a second turn directly (bypassing the
    # normal auto-cancel-on-new-speech_start path in `handle_frame`) so both
    # turn_a and turn_b are simultaneously in `_turn_tasks` for cancel_all to
    # cancel together.
    tm._events_broker = broker_b
    turn_b = "turn-b-manual"
    tm._turn_tasks[turn_b] = asyncio.create_task(tm._run_session_turn(turn_b, "outra pergunta"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert turn_b in tm._turn_tasks

    await tm.cancel_all()

    # Important #10: `cancel_all()` awaits every task to completion, so by
    # the time it returns each turn's `finally` has already discarded it
    # from `_cancelled_turns` — no permanent leak.
    assert turn_a not in tm._cancelled_turns
    assert turn_b not in tm._cancelled_turns
    assert tm._turn_tasks == {}

@pytest.mark.asyncio
async def test_unbind_session_returns_to_global_mode():
    sent = []
    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    await tm.bind_session("sess-1")
    tm.unbind_session()
    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    if tm._turn_tasks:
        await asyncio.gather(*tm._turn_tasks.values())
    deltas = [m for m in sent if m["type"] == "assistant.text.delta"]
    assert len(deltas) == 1
    assert "pagamentos" in deltas[0]["text"]


def _metric_records(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("voice.turn.metrics")]


@pytest.mark.asyncio
async def test_turn_metrics_logged_for_completed_global_turn(monkeypatch, caplog):
    """Task 11 (spec §22): a completed global-mode turn logs exactly one
    `voice.turn.metrics` line, with `voice_first_response_ms` (Voice TTFR =
    `audio_first_play_at - speech_ended_at`) matching the mocked clock."""
    _patch_monotonic(monkeypatch)
    sent = []
    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    with caplog.at_level(logging.INFO, logger="app.voice.turn_manager"):
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)
        if tm._turn_tasks:
            await asyncio.gather(*tm._turn_tasks.values())

    records = _metric_records(caplog)
    assert len(records) == 1
    metrics = records[0].args
    # speech_started_at=0.0, speech_ended_at=0.1, transcript_final_at=0.2,
    # route_started_at=0.3, agent_first_token_at=0.4, tts_first_chunk_at=0.5,
    # audio_first_play_at=0.6 (2nd TTS chunk's marks are no-ops via
    # `setdefault`) -> voice TTFR = 500ms. `route_started_at` fix (orchestrator,
    # post-implementer): brief omitted marking it in global mode, leaving
    # `routing_ms`/`agent_ttft_ms` always None for that mode — a real gap
    # given spec §23's "consultas simples" scenario is exactly global mode.
    assert metrics["voice_first_response_ms"] == 500.0
    assert metrics["routing_ms"] == 100.0
    assert metrics["agent_ttft_ms"] == 100.0


@pytest.mark.asyncio
async def test_turn_metrics_logged_for_completed_session_turn(monkeypatch, caplog):
    """Same acceptance scenario in session mode: `agent_ttft_ms` and
    `tts_first_audio_ms` must be present and coherent (derived from the
    streamed-reply timestamps, not the global-mode single-shot ones)."""
    _patch_monotonic(monkeypatch)
    sent = []

    class FakeTtsClient:
        async def synthesize(self, text: str) -> tuple[bytes, int]:
            return b"\x00\x01", 22050

    broker = FakeSessionBroker()

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    with caplog.at_level(logging.INFO, logger="app.voice.turn_manager"):
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)
        turn_id = tm._turn_id
        for _ in range(5):
            await asyncio.sleep(0)

        await broker.queue.put(
            {"session": "sess-1", "seq": 1, "text": "Encontrei o problema.", "line_type": "agent"}
        )
        for _ in range(5):
            await asyncio.sleep(0)
        await broker.queue.put(
            {"session": "sess-1", "seq": 2, "text": " Segue a explicação completa.", "line_type": "agent"}
        )
        await broker.queue.put(
            {"type": "session_status", "session": "sess-1", "status": "waiting_input"}
        )
        await tm._turn_tasks[turn_id]

    records = _metric_records(caplog)
    assert len(records) == 1
    metrics = records[0].args
    assert metrics["turnId"] == turn_id
    assert metrics["agent_ttft_ms"] is not None
    assert metrics["tts_first_audio_ms"] is not None
    assert metrics["agent_ttft_ms"] > 0
    assert metrics["tts_first_audio_ms"] > 0
    assert metrics["total_turn_ms"] >= metrics["voice_first_response_ms"]


@pytest.mark.asyncio
async def test_turn_metrics_not_logged_for_cancelled_turn(monkeypatch, caplog):
    """A turn cancelled before `audio_first_play_at` (mid-`route_global`,
    reusing the existing cancel-during-routing scenario) must not log
    `voice.turn.metrics` — nothing useful to report (brief §"_log_turn_metrics")."""
    _patch_monotonic(monkeypatch)
    sent = []
    reached_list_active = asyncio.Event()
    resume = asyncio.Event()

    class SlowSessionsRepo:
        async def list_active(self):
            reached_list_active.set()
            await resume.wait()
            return []

    tm = TurnManager(
        send_json=lambda msg: sent.append(msg),
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=SlowSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )

    async def feed():
        for i in range(23):
            await tm.handle_frame(b"\x00" * 640, index=i)

    with caplog.at_level(logging.INFO, logger="app.voice.turn_manager"):
        task = asyncio.create_task(feed())
        await reached_list_active.wait()
        turn_id = tm._turn_id
        await tm.cancel(turn_id)
        resume.set()
        await task

    assert _metric_records(caplog) == []


@pytest.mark.asyncio
async def test_turn_timings_cleaned_up_after_turn_ends(monkeypatch):
    """No leak in `_turn_timings`: empty after a completed turn and after a
    cancelled one (each run on its own `TurnManager` instance)."""
    _patch_monotonic(monkeypatch)

    # Completed turn (global mode).
    tm_completed = TurnManager(
        send_json=lambda msg: None,
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    for i in range(23):
        await tm_completed.handle_frame(b"\x00" * 640, index=i)
    if tm_completed._turn_tasks:
        await asyncio.gather(*tm_completed._turn_tasks.values())
    assert tm_completed._turn_timings == {}

    # Cancelled turn (mid-routing, same pattern as the "not logged" test).
    reached_list_active = asyncio.Event()
    resume = asyncio.Event()

    class SlowSessionsRepo:
        async def list_active(self):
            reached_list_active.set()
            await resume.wait()
            return []

    tm_cancelled = TurnManager(
        send_json=lambda msg: None,
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=SlowSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )

    async def feed():
        for i in range(23):
            await tm_cancelled.handle_frame(b"\x00" * 640, index=i)

    task = asyncio.create_task(feed())
    await reached_list_active.wait()
    turn_id = tm_cancelled._turn_id
    await tm_cancelled.cancel(turn_id)
    resume.set()
    await task
    bg_task = tm_cancelled._turn_tasks.get(turn_id)
    if bg_task is not None:
        await bg_task
    assert tm_cancelled._turn_timings == {}


@pytest.mark.asyncio
async def test_cancelled_turns_empty_after_turn_completes_normally():
    """Important #10 regression guard: a turn that completes normally (never
    cancelled) leaves `_cancelled_turns` empty — the discard calls added
    alongside every teardown point are no-ops on the happy path."""
    tm = TurnManager(
        send_json=lambda msg: None,
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
    )
    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    if tm._turn_tasks:
        await asyncio.gather(*tm._turn_tasks.values())
    assert tm._cancelled_turns == set()


@pytest.mark.asyncio
async def test_cancelled_turns_discarded_after_session_turn_task_finishes():
    """Important #10: a cancelled session-mode turn is removed from
    `_cancelled_turns` once `_run_session_turn`'s task fully finishes —
    no permanent leak of cancelled turn ids."""
    broker = FakeSessionBroker()

    async def fake_publish_command_fn(**kwargs):
        return "cmd-1"

    tm = TurnManager(
        send_json=lambda msg: None,
        send_binary=lambda b: None,
        stt_client=FakeSttClient(),
        tts_client=FakeTtsClient(),
        vad_is_speech=FakeVad().is_speech,
        sessions_repo=FakeSessionsRepo(),
        silence_ms_to_end=400,
        frame_ms=20,
        events_broker=broker,
        publish_command_fn=fake_publish_command_fn,
    )
    await tm.bind_session("sess-1")

    for i in range(23):
        await tm.handle_frame(b"\x00" * 640, index=i)
    turn_id = tm._turn_id
    for _ in range(5):
        await asyncio.sleep(0)
    assert turn_id in tm._turn_tasks  # blocked on the broker queue

    task = tm._turn_tasks[turn_id]
    await tm.cancel(turn_id)
    await asyncio.wait_for(task, timeout=2)

    assert turn_id not in tm._cancelled_turns
