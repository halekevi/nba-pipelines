# propOracle web UI (Flask). Optional local/CI image; Railway uses Nixpacks (see railway.toml).
# Build from REPO ROOT. .dockerignore keeps this small — huge sport CSV trees are omitted.
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN chmod +x /app/scripts/railway_gunicorn.sh

# Railway injects PORT at runtime
EXPOSE 8080

# Keep in sync with railway.toml / Procfile (scripts/railway_gunicorn.sh).
CMD ["/app/scripts/railway_gunicorn.sh"]
