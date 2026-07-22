FROM python:3.12-slim

# SHA curto do commit deployado (build arg do docker-compose) — exposto em
# runtime via env, lido por app/config.py e servido em GET /version.
ARG GIT_SHA=unknown
ENV SESSIONFLOW_GIT_SHA=$GIT_SHA

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
