# SkyShift API (server/) for Cloud Run. Built from the repo root because the image needs core/.
#   docker build -t skyshift-api . && docker run -p 8080:8080 -e SKYSHIFT_CACHE=/tmp/cache skyshift-api
# Deploy: infra/run/deploy.sh (gcloud run deploy --source . uses this file).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    SKYSHIFT_REPO_ROOT=/app \
    PORT=8080

WORKDIR /app

# Dependencies first (cached layer): only the files uv needs to resolve them.
COPY core/pyproject.toml core/uv.lock core/
COPY server/pyproject.toml server/uv.lock server/
COPY core/src core/src
RUN cd server && uv sync --frozen --no-dev --no-install-project

# App code and the data files the app reads.
COPY server/src server/src
COPY fixtures/api fixtures/api
COPY scripts/validate/data/targets.json scripts/validate/data/targets.json
RUN cd server && uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

WORKDIR /app/server
# Two worker processes: Python runs one thread at a time per process, so one process used about
# one of the two vCPUs (a frames build's stacking took 12 s instead of 0.3 s next to three object
# builds, 14 Nov). WEB_CONCURRENCY overrides it.
CMD ["sh", "-c", "exec /app/server/.venv/bin/uvicorn skyshift_server.app:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
