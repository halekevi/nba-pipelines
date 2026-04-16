#!/bin/sh
# Single entrypoint for Railway (Dockerfile + railway.toml). Fails fast with a log line if import breaks.
set -e
PORT="${PORT:-8080}"
echo "[proporacle] PORT=${PORT} cwd=$(pwd)"
python -c "from ui_runner.app import app as _app; print('[proporacle] Flask import OK')" || {
  echo "[proporacle] FATAL: Flask import failed (see traceback above)"
  exit 1
}

# /dashboard/income reads proporacle_income.db; populate from committed graded_props_*.json
# (SQLite is not in git — ingest each deploy). Skip if templates have no graded JSON.
if python -c "import pathlib,sys; sys.exit(0 if any(pathlib.Path('ui_runner/templates').glob('graded_props_*.json')) else 1)"; then
  echo "[proporacle] Ingesting graded_props → proporacle_income.db ..."
  python scripts/ingest_graded_to_income_db.py --all --purge-demo
else
  echo "[proporacle] Skip income ingest (no ui_runner/templates/graded_props_*.json)"
fi

exec python -m gunicorn ui_runner.app:app \
  --bind "0.0.0.0:${PORT}" \
  --workers 1 \
  --threads 4 \
  --worker-class gthread \
  --timeout 180 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
