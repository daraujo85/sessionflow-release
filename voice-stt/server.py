"""Standalone STT service: faster-whisper, model loaded once at startup,
warm across requests (spec §9 "não criar um novo processo/modelo para cada
fala"). One endpoint, raw PCM16LE mono 16kHz in, text out.
"""

from __future__ import annotations

import os

import numpy as np
from fastapi import FastAPI, Request
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("VOICE_STT_MODEL", "small")
DEVICE = os.environ.get("VOICE_STT_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("VOICE_STT_COMPUTE", "int8")

app = FastAPI()
_model: WhisperModel | None = None


@app.on_event("startup")
def load_model() -> None:
    global _model
    _model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)


@app.post("/transcribe")
async def transcribe(request: Request) -> dict:
    raw = await request.body()
    pcm = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    # vad_filter: Silero VAD embutido no faster-whisper descarta trechos sem
    # fala antes de decodificar — sem isso, silêncio/ruído vira hallucination
    # clássica do Whisper (ex.: "Legendas pela comunidade de Amara.org", texto
    # de treino que não veio do usuário).
    segments, _info = _model.transcribe(pcm, language="pt", beam_size=1, vad_filter=True)
    text = "".join(s.text for s in segments).strip()
    return {"text": text}
