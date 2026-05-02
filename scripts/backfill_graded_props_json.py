"""
Emit graded_props_YYYY-MM-DD.json for a slate date from existing graded xlsx files.
Does not rebuild slate_eval HTML (fast). Use when you only need the Prop Evaluation tab.

Usage:
  py -3 scripts/backfill_graded_props_json.py
  py -3 scripts/backfill_graded_props_json.py --date 2026-04-05
  py -3 scripts/backfill_graded_props_json.py --all-dates
"""
from __future__ import annotations

import argparse
import re
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
    load_merged_nba_graded_rows,
)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def backfill_one_date(date_str: str, templates: Path) -> bool:
    nba_rows = load_merged_nba_graded_rows(date_str)
    cbb_path = find_graded_file("cbb", date_str)
    nhl_path = find_graded_file("nhl", date_str)
    soccer_path = find_graded_file("soccer", date_str)
    mlb_path = find_graded_file("mlb", date_str)

    if not any([nba_rows, cbb_path, nhl_path, soccer_path, mlb_path]):
        return False

    bundles: list[tuple[str, list[dict]]] = []
    if nba_rows:
        bundles.append(("NBA", nba_rows))
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
    return True


def iter_output_dates() -> list[str]:
    """Subdirs of outputs/ named YYYY-MM-DD."""
    out = ROOT / "outputs"
    if not out.is_dir():
        return []
    dates: list[str] = []
    for p in sorted(out.iterdir()):
        if p.is_dir() and ISO_DATE.match(p.name):
            dates.append(p.name)
    return dates


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--date",
        default="",
        help="Slate date YYYY-MM-DD (default: yesterday unless --all-dates)",
    )
    p.add_argument(
        "--all-dates",
        action="store_true",
        help="Regenerate graded_props for every outputs/YYYY-MM-DD that has graded_*.xlsx",
    )
    args = p.parse_args()

    templates = ROOT / "ui_runner" / "templates"

    if args.all_dates:
        dates = iter_output_dates()
        if not dates:
            print("No outputs/YYYY-MM-DD folders found.")
            sys.exit(0)
        ok_n = 0
        skip_n = 0
        for d in dates:
            if backfill_one_date(d, templates):
                ok_n += 1
            else:
                skip_n += 1
                print(f"SKIP {d} (no graded_*.xlsx)")
        print(f"Done: {ok_n} rebuilt, {skip_n} skipped.")
        return

    if args.date.strip():
        date_str = args.date.strip()
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    if not backfill_one_date(date_str, templates):
        print(f"ERROR: No graded_*.xlsx found under outputs/{date_str}/")
        print("  Run scripts/run_grader.ps1 -Date", date_str, "first.")
        sys.exit(1)


if __name__ == "__main__":
    main()
