# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv "$VIRTUAL_ENV"

COPY backend/requirements.lock backend/requirements-api.txt /tmp/
RUN python -m pip install \
    --requirement /tmp/requirements.lock \
    --requirement /tmp/requirements-api.txt

COPY backend /src/backend
RUN python -m pip install --no-deps --no-build-isolation /src/backend

FROM python:3.12-slim-bookworm AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEMO_MODE=true \
    APP_ENV=production \
    DATA_DIR=/app/data \
    ARTIFACTS_DIR=/app/artifacts \
    DATABASE_URL=sqlite:////app/var/aml-agent.sqlite3 \
    BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8000

RUN groupadd --system --gid 10001 aml-agent \
    && useradd --system --uid 10001 --gid aml-agent --home-dir /app aml-agent \
    && mkdir -p /app/data /app/artifacts /app/var \
    && chown -R aml-agent:aml-agent /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=aml-agent:aml-agent backend /app/backend
COPY --chown=aml-agent:aml-agent data /app/data

WORKDIR /app
USER aml-agent
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
