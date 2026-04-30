#!/usr/bin/env python3
"""
Railway / Docker entry: optionally ingest graded JSON in the background, then exec gunicorn.

We do not import ui_runner.app here: gunicorn loads it in the worker. A pre-import doubled RAM and
import time and could OOM or exceed Railway's /ping health window.

Income ingest used to run synchronously before gunicorn and blocked PORT until completion.
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

    port = os.environ.get("PORT", "8080")
    tmpl = REPO / "ui_runner" / "templates"
    ingest_script = REPO / "scripts" / "ingest_graded_to_income_db.py"
    if tmpl.is_dir() and next(tmpl.glob("graded_props_*.json"), None) and ingest_script.is_file():
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
