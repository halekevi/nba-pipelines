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


def _patch_legacy_slate_min_height() -> None:
    """
    One-time startup backfill for stale slate_eval HTML already on disk/volume.

    Older generated files can keep iframe height inflated via min-height:100% / 100vh.
    New generator output no longer emits those rules, but existing files on a mounted
    volume may persist across deploys; patching them here keeps behavior consistent.
    """
    # Allow explicit opt-out if needed for debugging.
    if os.environ.get("PROPORACLE_PATCH_SLATE_MIN_HEIGHT", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        print("[proporacle] Slate min-height backfill disabled by env.", flush=True)
        return

    candidates: list[Path] = [
        REPO / "ui_runner" / "templates",
        REPO / "ui_runner" / "templates" / "archive",
        Path("/app/data"),
        Path("/app/web-volume"),
    ]
    for key in ("RAILWAY_VOLUME_MOUNT_PATH", "PROPORACLE_PERSISTENT_DATA_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            candidates.append(Path(raw))

    seen: set[str] = set()
    roots: list[Path] = []
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        if rp.exists():
            roots.append(rp)

    if not roots:
        return

    patched_files = 0
    scanned_files = 0
    for root in roots:
        try:
            for path in root.rglob("slate_eval_*.html"):
                scanned_files += 1
                try:
                    original = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                updated = (
                    original.replace("min-height:100vh", "min-height:0")
                    .replace("min-height:100%", "min-height:0")
                    .replace("height:100%", "height:auto")
                )
                if updated == original:
                    continue
                try:
                    path.write_text(updated, encoding="utf-8")
                    patched_files += 1
                except OSError:
                    continue
        except OSError:
            continue

    print(
        f"[proporacle] Slate min-height backfill scanned={scanned_files} patched={patched_files}",
        flush=True,
    )


def main() -> None:
    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))

    _patch_legacy_slate_min_height()

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
