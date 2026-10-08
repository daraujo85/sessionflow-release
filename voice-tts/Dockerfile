FROM python:3.12-slim

# Piper binary + voice model (Linux x86_64 — this image only runs on Linux
# containers) — same release/voice as worker/sessionflow_worker/jarvis.py
# (_PIPER_RELEASE_BASE/_PIPER_VOICE_BASE, asset piper_linux_x86_64.tar.gz).
# server.py defaults expect the binary at /piper/piper and the model at
# /piper/pt_BR-faber-medium.onnx (VOICE_TTS_PIPER_BIN/VOICE_TTS_PIPER_MODEL).
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN curl -L https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz \
      | tar -xz -C / \
    && curl -L -o /piper/pt_BR-faber-medium.onnx \
      https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx \
    && curl -L -o /piper/pt_BR-faber-medium.onnx.json \
      https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (better layer caching)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY server.py ./
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8002

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
