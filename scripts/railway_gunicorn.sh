#!/bin/sh
# Single entrypoint for Railway (Dockerfile + railway.toml). Fails fast if import breaks.
# Income ingest runs in the background (see scripts/railway_serve.py) so /ping succeeds while DB is filled.
set -e
PORT="${PORT:-8080}"
echo "[proporacle] PORT=${PORT} cwd=$(pwd)"
exec python scripts/railway_serve.py
