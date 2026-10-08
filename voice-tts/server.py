"""Standalone TTS service: Piper CLI invocation per synthesis call (see
ponytail note in `synthesize`).

Mirrors `worker/sessionflow_worker/jarvis.py::_synth_piper_sync` (same CLI
invocation pattern) but lives in its own service per spec §17 — the per-host
Worker's Piper stays dedicated to the existing JARVIS batch flow.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave

from fastapi import FastAPI, Request
from fastapi.responses import Response

PIPER_BIN = os.environ.get("VOICE_TTS_PIPER_BIN", "/piper/piper")
PIPER_MODEL = os.environ.get("VOICE_TTS_PIPER_MODEL", "/piper/pt_BR-faber-medium.onnx")

app = FastAPI()


# ponytail: one Piper process per call (correct, ~model-load cost per
# sentence chunk). A persistent Piper process would need reliable
# per-utterance framing over --output-raw, which the CLI doesn't provide
# out of the box — upgrade when synthesis latency becomes a measured
# problem, not before (YAGNI).
@app.post("/synthesize")
async def synthesize(request: Request) -> Response:
    body = await request.json()
    text = body["text"]
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(PIPER_BIN)}
        subprocess.run(
            [PIPER_BIN, "--model", PIPER_MODEL, "--output_file", tmp.name],
            input=text.encode("utf-8"),
            check=True,
            env=env,
        )
        with wave.open(tmp.name, "rb") as wav:
            pcm = wav.readframes(wav.getnframes())
            sample_rate = wav.getframerate()
    return Response(
        content=pcm,
        media_type="application/octet-stream",
        headers={"X-Sample-Rate": str(sample_rate)},
    )
