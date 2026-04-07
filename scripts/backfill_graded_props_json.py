#!/usr/bin/env python3
"""
Rebuild graded_props_<date>.json from graded_*.xlsx under outputs/<date>/
(without re-running the full HTML builder).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR / "grading"))

from build_grades_html import export_graded_props_json, load_graded  # noqa: E402

_PREFIX_SPORT: list[tuple[str, str]] = [
    ("graded_nba1h_", "NBA"),
    ("graded_nba1q_", "NBA"),
    ("graded_nba_", "NBA"),
    ("graded_wcbb_", "WCBB"),
    ("graded_cbb_", "CBB"),
    ("graded_nhl_", "NHL"),
    ("graded_soccer_", "Soccer"),
    ("graded_mlb_", "MLB"),
]


def collect_bundles(date_str: str) -> list[tuple[str, list[dict]]]:
    out_day = REPO_ROOT / "outputs" / date_str
    if not out_day.is_dir():
        return []
    bundles: list[tuple[str, list[dict]]] = []
    used: set[Path] = set()
    for path in sorted(out_day.glob(f"graded_*_{date_str}.xlsx")):
        if path in used:
            continue
        name = path.name.lower()
        sport: str | None = None
        for pref, sp in _PREFIX_SPORT:
            if name.startswith(pref.lower()):
                sport = sp
                break
        if not sport:
            continue
        rows = load_graded(path)
        bundles.append((sport, rows))
        used.add(path)
    return bundles


def main() -> None:
    ap = argparse.ArgumentParser(description="Write graded_props_<date>.json from graded xlsx files.")
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    ap.add_argument(
        "--out",
        default="",
        help="Output directory (default: ui_runner/templates)",
    )
    args = ap.parse_args()
    date_str = args.date.strip()
    out_dir = Path(args.out).resolve() if args.out else REPO_ROOT / "ui_runner" / "templates"
    bundles = collect_bundles(date_str)
    if not bundles:
        print(f"No graded_*_{date_str}.xlsx found under outputs/{date_str}/")
        sys.exit(1)
    out = export_graded_props_json(date_str, out_dir, bundles)
    print(out)


if __name__ == "__main__":
    main()
