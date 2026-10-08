"""Pure endpointing state machine — decoupled from the actual VAD model so it
is unit-testable without audio (spec §8).

The caller feeds one `is_speech` bool per fixed-size frame (20ms, matching the
capture worklet's frame size) from whatever VAD decides voice activity
(webrtcvad/Silero). This class only tracks the "speech / silence run" timing
and emits the two events the turn manager cares about.
"""

from __future__ import annotations

from typing import Literal

EndpointEvent = Literal["speech_start", "utterance_end"]


class Endpointer:
    def __init__(self, *, silence_ms_to_end: int = 450, frame_ms: int = 20) -> None:
        self._silence_frames_to_end = max(1, round(silence_ms_to_end / frame_ms))
        self._in_speech = False
        self._silence_run = 0

    def feed(self, *, is_speech: bool) -> EndpointEvent | None:
        if is_speech:
            self._silence_run = 0
            if not self._in_speech:
                self._in_speech = True
                return "speech_start"
            return None
        if not self._in_speech:
            return None
        self._silence_run += 1
        if self._silence_run >= self._silence_frames_to_end:
            self._in_speech = False
            self._silence_run = 0
            return "utterance_end"
        return None

    def reset(self) -> None:
        self._in_speech = False
        self._silence_run = 0
