"""HTTP client for the standalone `voice-tts` service."""

from __future__ import annotations

import httpx


class TtsClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def synthesize(self, text: str) -> tuple[bytes, int]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{self._base_url}/synthesize", json={"text": text})
            resp.raise_for_status()
            sample_rate = int(resp.headers["X-Sample-Rate"])
            return resp.content, sample_rate
