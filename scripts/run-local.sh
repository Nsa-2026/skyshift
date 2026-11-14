#!/usr/bin/env bash
# One launch entry; optional explicit runtime overrides for managed environments.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export SKYSHIFT_JOBS="${SKYSHIFT_JOBS:-$ROOT/.runtime/jobs}"
export SKYSHIFT_SCIENCE_CACHE="${SKYSHIFT_SCIENCE_CACHE:-$ROOT/.runtime/science}"
export SKYSHIFT_CACHE="${SKYSHIFT_CACHE:-$ROOT/.runtime/api}"
export SKYSHIFT_ENABLE_SCIENCE_JOBS="${SKYSHIFT_ENABLE_SCIENCE_JOBS:-1}"
export SKYSHIFT_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/core/src:$ROOT/server/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
if [[ ! -d web/dist || "${SKYSHIFT_BUILD:-0}" == 1 ]]; then
  (cd web && npm ci && npm run build)
fi
if [[ -n "${SKYSHIFT_PYTHON:-}" ]]; then
  exec "$SKYSHIFT_PYTHON" -m uvicorn skyshift_server.app:app --host 127.0.0.1 --port "${PORT:-8083}"
fi
exec uv run --project "$ROOT/server" --frozen uvicorn skyshift_server.app:app --host 127.0.0.1 --port "${PORT:-8083}"
