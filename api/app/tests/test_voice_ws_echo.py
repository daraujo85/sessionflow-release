"""WS transport-level coverage for /ws/voice.

Etapa 1 shipped a pure echo body to validate transport before VAD/STT/TTS
existed; Task 4 replaced it with `TurnManager`. This file keeps the transport
assertions (ticket-gated connection) and updates the "does the socket do
something" test to assert the new `transcript.final` round-trip through a
stubbed STT client + VAD, instead of the raw echo.
"""

import itertools

import pytest
import webrtcvad
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.voice.stt_client import SttClient


def _test_settings() -> Settings:
    """Settings with auth disabled for testing."""
    return Settings(auth_email="", auth_password="")


@pytest.fixture
def app_test():
    """Test app with auth disabled."""
    return create_app(settings=_test_settings())


def test_ws_voice_rejects_missing_ticket(app_test):
    client = TestClient(app_test)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_text()
    # Check that the server closed with the expected policy violation code
    assert exc_info.value.code == 4401


def test_ws_voice_emits_transcript_final_with_valid_ticket(app_test, monkeypatch):
    """One utterance (3 speech frames + enough silence to end it) round-trips
    through the real `TurnManager` + `webrtcvad.Vad` wiring in the router and
    comes back as a single `transcript.final`, using a stubbed `SttClient` so
    no real HTTP call hits the voice-stt service."""

    async def fake_transcribe(self, pcm: bytes) -> str:
        return "quais sessões estão ativas"

    monkeypatch.setattr(SttClient, "transcribe", fake_transcribe)

    # This test doesn't exercise the real lifespan (no live Mongo in the unit
    # suite), so `app.state.mongo_db` is never set. `SessionsRepository` is
    # only constructed, never queried against a real collection here — stub
    # it out so the router's `db[collection_name]` access has something to
    # subscript.
    app_test.state.mongo_db = None

    class _FakeSessionsRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def list_active(self):
            return []

    monkeypatch.setattr(
        "app.routers.voice.SessionsRepository", _FakeSessionsRepo
    )

    # Speech for the first 3 frames, silence after — mirrors
    # app/tests/test_voice_turn_manager.py's FakeVad, driven through the real
    # Endpointer's default timing as wired in the router (450ms / 20ms frames
    # => round(450/20) = 22 silence frames to end the utterance).
    calls = itertools.count()

    def fake_is_speech(self, frame, sample_rate):
        return next(calls) < 3

    monkeypatch.setattr(webrtcvad.Vad, "is_speech", fake_is_speech)

    client = TestClient(app_test)
    resp = client.post("/api/voice/ticket")
    assert resp.status_code == 200
    ticket = resp.json()["ticket"]
    with client.websocket_connect(f"/ws/voice?ticket={ticket}") as ws:
        for _ in range(3 + 22):
            ws.send_bytes(b"\x00" * 640)
        msg = ws.receive_json()
    assert msg["type"] == "transcript.final"
    assert msg["text"] == "quais sessões estão ativas"


def test_ws_voice_bind_session_control_message_switches_turn_manager_to_session_mode(
    app_test, monkeypatch
):
    """`voice.bind_session` (spec §6) must reach `TurnManager.bind_session`
    with the given `sessionId`, and mic frames after it must still flow into
    `handle_frame` — proven via a `TurnManager` stand-in since the real one's
    session-mode streaming is covered by `test_voice_turn_manager.py`."""
    calls: list[tuple[str, object]] = []

    class FakeTurnManager:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def bind_session(self, session_id):
            calls.append(("bind_session", session_id))

        async def handle_frame(self, frame, *, index):
            calls.append(("handle_frame", index))

        async def cancel(self, turn_id):
            calls.append(("cancel", turn_id))

        async def cancel_all(self):
            calls.append(("cancel_all", None))

    monkeypatch.setattr("app.routers.voice.TurnManager", FakeTurnManager)

    app_test.state.mongo_db = None
    app_test.state.events_broker = object()

    class _FakeSessionsRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr("app.routers.voice.SessionsRepository", _FakeSessionsRepo)

    client = TestClient(app_test)
    resp = client.post("/api/voice/ticket")
    ticket = resp.json()["ticket"]
    with client.websocket_connect(f"/ws/voice?ticket={ticket}") as ws:
        ws.send_json({"type": "voice.bind_session", "sessionId": "sess-42"})
        ws.send_bytes(b"\x00" * 640)

    assert ("bind_session", "sess-42") in calls
    assert ("handle_frame", 0) in calls
    init_kwargs = next(kwargs for name, kwargs in calls if name == "init")
    assert init_kwargs["events_broker"] is app_test.state.events_broker
    assert callable(init_kwargs["publish_command_fn"])


def test_ws_voice_disconnect_calls_turn_manager_cancel_all(app_test, monkeypatch):
    """Spec §21: on WS disconnect the router must cancel the current turn.
    `TurnManager.cancel_all()` is the mechanism (see test_voice_turn_manager.py);
    prove the router actually calls it from the `WebSocketDisconnect` handler."""
    calls: list[tuple[str, object]] = []

    class FakeTurnManager:
        def __init__(self, **kwargs):
            pass

        async def bind_session(self, session_id):
            calls.append(("bind_session", session_id))

        async def handle_frame(self, frame, *, index):
            calls.append(("handle_frame", index))

        async def cancel(self, turn_id):
            calls.append(("cancel", turn_id))

        async def cancel_all(self):
            calls.append(("cancel_all", None))

    monkeypatch.setattr("app.routers.voice.TurnManager", FakeTurnManager)

    app_test.state.mongo_db = None
    app_test.state.events_broker = object()

    class _FakeSessionsRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr("app.routers.voice.SessionsRepository", _FakeSessionsRepo)

    client = TestClient(app_test)
    resp = client.post("/api/voice/ticket")
    ticket = resp.json()["ticket"]
    with client.websocket_connect(f"/ws/voice?ticket={ticket}") as ws:
        ws.send_json({"type": "voice.bind_session", "sessionId": "sess-42"})
        ws.send_bytes(b"\x00" * 640)
    # `with` block exit closes the client-side socket, triggering
    # WebSocketDisconnect on the server's `receive()` loop.

    assert ("cancel_all", None) in calls

def test_ws_voice_handle_frame_error_still_calls_cancel_all(app_test, monkeypatch):
    """Critical #4: any exception other than WebSocketDisconnect (a
    malformed VAD frame, an STT/TTS error, bad control JSON) must still run
    `cancel_all()` before propagating — otherwise a session-mode background
    turn leaks forever."""
    calls: list[tuple[str, object]] = []

    class FakeTurnManager:
        def __init__(self, **kwargs):
            pass

        async def bind_session(self, session_id):
            calls.append(("bind_session", session_id))

        async def handle_frame(self, frame, *, index):
            calls.append(("handle_frame", index))
            raise RuntimeError("boom")

        async def cancel(self, turn_id):
            calls.append(("cancel", turn_id))

        async def cancel_all(self):
            calls.append(("cancel_all", None))

    monkeypatch.setattr("app.routers.voice.TurnManager", FakeTurnManager)

    app_test.state.mongo_db = None
    app_test.state.events_broker = object()

    class _FakeSessionsRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr("app.routers.voice.SessionsRepository", _FakeSessionsRepo)

    client = TestClient(app_test)
    resp = client.post("/api/voice/ticket")
    ticket = resp.json()["ticket"]
    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect(f"/ws/voice?ticket={ticket}") as ws:
            ws.send_bytes(b"\x00" * 640)
            ws.receive_text()

    assert ("cancel_all", None) in calls


def test_ws_voice_ping_control_message_is_a_no_op(app_test, monkeypatch):
    """Keepalive `{"type": "ping"}` (frontend sends one every 20s) must not
    reach `TurnManager` and must not break the frame loop that follows it."""
    calls: list[tuple[str, object]] = []

    class FakeTurnManager:
        def __init__(self, **kwargs):
            pass

        async def bind_session(self, session_id):
            calls.append(("bind_session", session_id))

        async def handle_frame(self, frame, *, index):
            calls.append(("handle_frame", index))

        async def cancel(self, turn_id):
            calls.append(("cancel", turn_id))

        async def cancel_all(self):
            calls.append(("cancel_all", None))

    monkeypatch.setattr("app.routers.voice.TurnManager", FakeTurnManager)

    app_test.state.mongo_db = None
    app_test.state.events_broker = object()

    class _FakeSessionsRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr("app.routers.voice.SessionsRepository", _FakeSessionsRepo)

    client = TestClient(app_test)
    resp = client.post("/api/voice/ticket")
    ticket = resp.json()["ticket"]
    with client.websocket_connect(f"/ws/voice?ticket={ticket}") as ws:
        ws.send_json({"type": "ping"})
        ws.send_bytes(b"\x00" * 640)
    # `with` block exit disconnects the socket, which now also triggers the
    # router's `cancel_all()` call (spec §21) — see
    # test_ws_voice_disconnect_calls_turn_manager_cancel_all for the dedicated test.

    assert calls == [("handle_frame", 0), ("cancel_all", None)]
