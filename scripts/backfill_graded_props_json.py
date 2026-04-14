"""
Emit graded_props_YYYY-MM-DD.json for a slate date from existing graded xlsx files.
Does not rebuild slate_eval HTML (fast). Use when you only need the Prop Evaluation tab.

Usage:
  py -3 scripts/backfill_graded_props_json.py
  py -3 scripts/backfill_graded_props_json.py --date 2026-04-05
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADING = ROOT / "scripts" / "grading"
sys.path.insert(0, str(GRADING))

from build_grades_html import (  # noqa: E402
    export_graded_props_json,
    find_graded_file,
    load_graded,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--date",
        default="",
        help="Slate date YYYY-MM-DD (default: yesterday)",
    )
    args = p.parse_args()
    if args.date.strip():
        date_str = args.date.strip()
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    nba_path = find_graded_file("nba", date_str)
    cbb_path = find_graded_file("cbb", date_str)
    nhl_path = find_graded_file("nhl", date_str)
    soccer_path = find_graded_file("soccer", date_str)
    mlb_path = find_graded_file("mlb", date_str)

    if not any([nba_path, cbb_path, nhl_path, soccer_path, mlb_path]):
        print(f"ERROR: No graded_*.xlsx found under outputs/{date_str}/")
        print("  Run scripts/run_grader.ps1 -Date", date_str, "first.")
        sys.exit(1)

    templates = ROOT / "ui_runner" / "templates"
    bundles: list[tuple[str, list[dict]]] = []
    if nba_path:
        bundles.append(("NBA", load_graded(nba_path)))
    if cbb_path:
        bundles.append(("CBB", load_graded(cbb_path)))
    if nhl_path:
        bundles.append(("NHL", load_graded(nhl_path)))
    if soccer_path:
        bundles.append(("Soccer", load_graded(soccer_path)))
    if mlb_path:
        bundles.append(("MLB", load_graded(mlb_path)))

    out = export_graded_props_json(date_str, templates, bundles)
    n_total = sum(len(rows) for _, rows in bundles)
    print(f"OK -> {out} ({n_total:,} rows)")


if __name__ == "__main__":
    main()
