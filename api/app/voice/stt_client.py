"""HTTP client for the standalone `voice-stt` service."""

from __future__ import annotations

import httpx


# Whisper `small` leva mais de 10 s em CPU/cold-start; este é o teto total
# da inferência, ainda finito para não deixar uma ligação pendurada.
STT_TIMEOUT_SECONDS = 45.0


class SttClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def transcribe(self, pcm: bytes) -> str:
        async with httpx.AsyncClient(timeout=STT_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{self._base_url}/transcribe", content=pcm)
            resp.raise_for_status()
            return resp.json()["text"]
