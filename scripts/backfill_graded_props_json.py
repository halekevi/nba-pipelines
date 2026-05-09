"""
Emit graded_props_YYYY-MM-DD.json (and by default slate_eval_*.html) for a slate date.

The Grades **Slate Evaluation** iframe loads `slate_eval_{date}.html`; **Prop Evaluation**
loads `graded_props_{date}.json`. Running JSON-only backfill without rebuilding HTML leaves
Tennis (or any new sport) visible in Prop Evaluation but missing from Slate Evaluation.

By default this script runs `build_grades_html.py` for the date (auto-detects all graded
workbooks including Tennis) then copies artifacts to `mobile/www` when present.

Usage:
  py -3 scripts/backfill_graded_props_json.py
  py -3 scripts/backfill_graded_props_json.py --date 2026-04-05
  py -3 scripts/backfill_graded_props_json.py --all-dates
  py -3 scripts/backfill_graded_props_json.py --date 2026-04-05 --json-only
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
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
    nba_family_bundles_for_json,
)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _copy_mobile_grades_artifacts(date_str: str, templates: Path) -> None:
    mobile_www = ROOT / "mobile" / "www"
    if not mobile_www.is_dir():
        return
    for name in (f"slate_eval_{date_str}.html", f"graded_props_{date_str}.json"):
        src = templates / name
        if src.is_file():
            shutil.copy2(src, mobile_www / name)


def rebuild_slate_and_json_via_build_grades_html(date_str: str, templates: Path) -> bool:
    """Run full HTML+JSON export (same as run_grader tail). Returns True on success."""
    script = GRADING / "build_grades_html.py"
    r = subprocess.run(
        [sys.executable, str(script), "--date", date_str, "--out", str(templates)],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        return False
    _copy_mobile_grades_artifacts(date_str, templates)
    print(f"OK slate_eval + graded_props -> {templates} (and mobile/www if present)")
    return True


def backfill_one_date(date_str: str, templates: Path, *, json_only: bool = False) -> bool:
    if not json_only:
        if rebuild_slate_and_json_via_build_grades_html(date_str, templates):
            return True
        print(
            f"  NOTE: build_grades_html.py failed or no graded workbooks for {date_str}; "
            "falling back to JSON-only merge.",
            flush=True,
        )

    bundles: list[tuple[str, list[dict]]] = []
    bundles.extend(nba_family_bundles_for_json(date_str))
    cbb_path = find_graded_file("cbb", date_str)
    nhl_path = find_graded_file("nhl", date_str)
    soccer_path = find_graded_file("soccer", date_str)
    mlb_path = find_graded_file("mlb", date_str)
    wnba_path = find_graded_file("wnba", date_str)
    tennis_path = find_graded_file("tennis", date_str)

    if cbb_path:
        bundles.append(("CBB", load_graded(cbb_path)))
    if nhl_path:
        bundles.append(("NHL", load_graded(nhl_path)))
    if soccer_path:
        bundles.append(("Soccer", load_graded(soccer_path)))
    if mlb_path:
        bundles.append(("MLB", load_graded(mlb_path)))
    if wnba_path:
        bundles.append(("WNBA", load_graded(wnba_path, "wnba")))
    if tennis_path:
        bundles.append(("Tennis", load_graded(tennis_path, "tennis")))

    if not bundles:
        return False

    out = export_graded_props_json(date_str, templates, bundles)
    n_total = sum(len(rows) for _, rows in bundles)
    print(f"OK -> {out} ({n_total:,} rows)")
    if not json_only:
        _copy_mobile_grades_artifacts(date_str, templates)
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
    p.add_argument(
        "--json-only",
        action="store_true",
        help="Only merge graded_props JSON from xlsx bundles; skip slate_eval HTML (Slate Evaluation may be stale).",
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
            if backfill_one_date(d, templates, json_only=args.json_only):
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

    if not backfill_one_date(date_str, templates, json_only=args.json_only):
        print(f"ERROR: No graded_*.xlsx found under outputs/{date_str}/")
        print("  Run scripts/run_grader.ps1 -Date", date_str, "first.")
        sys.exit(1)


if __name__ == "__main__":
    main()
