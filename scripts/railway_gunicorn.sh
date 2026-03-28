#!/bin/sh
# Single entrypoint for Railway (Dockerfile + railway.toml). Fails fast with a log line if import breaks.
set -e
PORT="${PORT:-8080}"
echo "[proporacle] PORT=${PORT} cwd=$(pwd)"
python -c "from ui_runner.app import app as _app; print('[proporacle] Flask import OK')" || {
  echo "[proporacle] FATAL: Flask import failed (see traceback above)"
  exit 1
}
exec python -m gunicorn ui_runner.app:app \
  --bind "0.0.0.0:${PORT}" \
  --workers 1 \
  --threads 4 \
  --worker-class gthread \
  --timeout 180 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
