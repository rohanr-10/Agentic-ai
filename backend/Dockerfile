# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Agentic Platform Backend - API image
# Multi-stage build, non-root runtime user, healthcheck via /health/live.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="agentic-platform-backend"
LABEL org.opencontainers.image.description="Backend for a conversational / autonomous agentic AI platform"

# security: dedicated non-root user
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv/app

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY requirements.txt ./

RUN chown -R app:app /srv/app
USER app

# data directories (mounted volumes in production)
RUN mkdir -p /srv/app/data/storage /srv/app/data/sandboxes

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4).status == 200 else 1)"

# Migrations are applied by the dedicated "migrate" service in compose.
# For single-container runs, set AGP_RUN_MIGRATIONS=true.
ENV AGP_STORAGE_LOCAL_ROOT=/srv/app/data/storage \
    AGP_SANDBOX_ROOT=/srv/app/data/sandboxes

CMD ["sh", "-c", "if [ \"$AGP_RUN_MIGRATIONS\" = \"true\" ]; then alembic upgrade head; fi; exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 --workers ${AGP_UVICORN_WORKERS:-2}"]
