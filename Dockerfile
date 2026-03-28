# propOracle web UI (Flask). Optional local/CI image; Railway uses Nixpacks (see railway.toml).
# Build from REPO ROOT. .dockerignore keeps this small — huge sport CSV trees are omitted.
FROM python:3.13-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

# Railway injects PORT at runtime
EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn ui_runner.app:app --bind 0.0.0.0:${PORT:-8080} --workers=2 --threads=4 --worker-class=gthread --timeout=120"]
