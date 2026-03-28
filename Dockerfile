# PropOracle — SlateIQ web UI (Flask). Build from REPO ROOT so `ui_runner` is a package.
# Railway: set builder to Dockerfile (railway.toml) or remove custom start command that overrides this image.
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8

COPY ui_runner/requirements.txt /tmp/ui-requirements.txt
RUN pip install --no-cache-dir -r /tmp/ui-requirements.txt

# Full repo (templates, static NBA/NHL paths for status fallbacks, etc.)
COPY . /app

# Railway injects PORT at runtime
EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn ui_runner.app:app --bind 0.0.0.0:${PORT:-8080} --workers=2 --threads=4 --worker-class=gthread --timeout=120"]
