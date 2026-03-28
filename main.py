"""
WSGI entry shim for hosts that run `gunicorn main:app` (Railway default / old custom commands).

The real app lives in ui_runner.app; this module only re-exports `app`.
"""
from ui_runner.app import app

__all__ = ["app"]
