"""
WSGI shim when Railway **Root Directory** is set to `ui_runner`.

Only that folder is copied to /app, so repo-root `main.py` is missing and
`gunicorn main:app` must load this file next to `app.py`.
"""
from app import app

__all__ = ["app"]
