#!/usr/bin/env python3
"""
Railway / Docker entry: verify Flask loads, optionally ingest graded JSON in the background, then exec gunicorn.

Running income ingest synchronously before gunicorn blocked PORT until completion; with many
graded_props_*.json files deploy healthchecks (/ping) timed out with 503.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))

    # Fail fast if the app cannot import (same signal as the old shell check).
    from ui_runner.app import app as _app  # noqa: F401

    port = os.environ.get("PORT", "8080")
    tmpl = REPO / "ui_runner" / "templates"
    ingest_script = REPO / "scripts" / "ingest_graded_to_income_db.py"
    if tmpl.is_dir() and any(tmpl.glob("graded_props_*.json")) and ingest_script.is_file():
        print(
            "[proporacle] Ingesting graded_props → proporacle_income.db (background; gunicorn starts now)…",
            flush=True,
        )
        subprocess.Popen(
            [sys.executable, str(ingest_script), "--all", "--purge-demo"],
            cwd=str(REPO),
            stdout=sys.stdout,
            stderr=sys.stderr,
            start_new_session=True,
        )

    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "gunicorn",
            "ui_runner.app:app",
            "--bind",
            f"0.0.0.0:{port}",
            "--workers",
            "1",
            "--threads",
            "4",
            "--worker-class",
            "gthread",
            "--timeout",
            "180",
            "--graceful-timeout",
            "30",
            "--access-logfile",
            "-",
            "--error-logfile",
            "-",
        ],
    )


if __name__ == "__main__":
    main()
