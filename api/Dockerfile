FROM python:3.12-slim

# Versão deployada (build args do docker-compose) — exposta em runtime via
# env, lida por app/config.py e servida em GET /version.
ARG GIT_SHA=unknown
ARG RELEASE_VERSION=unknown
ENV SESSIONFLOW_GIT_SHA=$GIT_SHA
ENV SESSIONFLOW_RELEASE_VERSION=$RELEASE_VERSION

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (better layer caching)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY app ./app
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
