#!/usr/bin/env python3
"""Merge WNBA step8 into slate_latest.json + slate_sport_wnba.json (Flask templates + mobile/www).

Run automatically at the end of scripts/run_wnba_pipeline.ps1. Can also run manually:

  py -3.14 scripts/publish_wnba_slate_to_ui.py --date 2026-05-08
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_scripts = ROOT / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

import combined_slate_tickets as cst  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish WNBA slate rows into web JSON (merge, do not wipe other sports).")
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    ap.add_argument(
        "--outdir",
        action="append",
        default=[],
        help="Extra directory to write slate_latest.json (repeatable). Default: ui_runner/templates and mobile/www if present.",
    )
    args = ap.parse_args()
    extra = [x for x in (args.outdir or []) if str(x).strip()]
    outdirs = extra if extra else None
    ok = cst.publish_wnba_slate_merge_into_web(args.date, web_outdirs=outdirs)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
