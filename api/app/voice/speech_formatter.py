"""display_text vs speech_text (spec §12): the agent's raw reply is fine to
show on screen, but Piper should never be handed markdown/code/URLs/stack
traces. Also chunks incremental text into short, sentence-shaped pieces so
TTS starts before the full reply exists.
"""

from __future__ import annotations

import re

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_URL_RE = re.compile(r"https?://\S+")
_STACK_TRACE_RE = re.compile(
    r"(?ms)^Traceback \(most recent call last\):\n"
    r"(?:^  File .*\n(?:^    .*\n)?)*"
    r"^[^\n]*(?:Error|Exception|Warning|Exit|Interrupt|Fault):[^\n]*(?:\n|$)"
)
_TABLE_ROW_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")
_SENTENCE_END_RE = re.compile(r"[.!?:]")

# Símbolo/marcação que um TTS leria em voz alta: markdown, box-drawing (TUI),
# bullet, seta. Mesma lista provada em `jarvis.py::_DROP_RE` (resumo falado
# do JARVIS) — porta pra cá porque o chunk de voz de sessão vem direto da
# tela capturada (output_capture.py) e carrega o mesmo tipo de chrome.
_DROP_RE = re.compile(
    r"[*_#`>\[\](){}<>|~^=+\\/•·●○◦◆■□▪▫▶►◀→⟶←↑↓✓✔✗✘✦✧★☆─-╿]"
)


def to_speech_text(display_text: str) -> str:
    text = _CODE_BLOCK_RE.sub(" ", display_text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _STACK_TRACE_RE.sub(" ", text)
    text = _TABLE_ROW_RE.sub(" ", text)
    text = text.replace("…", ". ").replace("‥", ". ")
    text = _DROP_RE.sub(" ", text)
    return " ".join(text.split())


class SentenceChunker:
    """Accumulates text deltas, yields complete chunks as soon as a sentence
    ends or the buffer crosses `max_chars` (spec §12)."""

    def __init__(self, *, max_chars: int = 100) -> None:
        self._max_chars = max_chars
        self._buf = ""

    def feed(self, text_delta: str) -> list[str]:
        self._buf += text_delta
        chunks: list[str] = []
        while True:
            chunk = self._try_extract()
            if chunk is None:
                break
            chunks.append(chunk)
        return chunks

    def _try_extract(self) -> str | None:
        stripped = self._buf.strip()
        if not stripped:
            return None
        match = _SENTENCE_END_RE.search(stripped)
        if match:
            cut = match.end()
            chunk, rest = stripped[:cut].strip(), stripped[cut:].strip()
            self._buf = rest
            return chunk
        if len(stripped) >= self._max_chars:
            # break at the last space before the cap so words aren't split
            cut = stripped.rfind(" ", 0, self._max_chars)
            cut = cut if cut > 0 else self._max_chars
            chunk, rest = stripped[:cut].strip(), stripped[cut:].strip()
            self._buf = rest
            return chunk
        return None

    def flush(self) -> str | None:
        stripped = self._buf.strip()
        self._buf = ""
        return stripped or None
