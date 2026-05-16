"""Backfill actuals + re-grade for a date range.

Use after fixing a grader / actuals fetcher bug (e.g. the NHL ESPN/NHL-API
duplicate-name dedupe issue) to refresh historical graded data.

Usage:
    python scripts/backfill_actuals_and_grade.py --sport NHL --start 2026-02-19 --end 2026-05-07
    python scripts/backfill_actuals_and_grade.py --sport SOCCER --start 2026-04-01 --end 2026-05-07 --skip-grade
    python scripts/backfill_actuals_and_grade.py --sport NHL --dates 2026-04-15 2026-04-16 2026-04-17

What it does, per date:
  1. Run scripts/fetch_actuals.py to refresh outputs/<date>/actuals_<sport>_<date>.csv
  2. Run scripts/nhl_soccer_grader.py to refresh outputs/<date>/graded_<sport>_<date>.xlsx
  3. Run scripts/backfill_graded_props_json.py --date <date> to refresh JSON
  4. Run scripts/grade_quality_audit.py --date <date> to verify

Errors on any step are logged and the run continues.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

SPORT_TO_SLUG = {
    "NHL": "nhl",
    "SOCCER": "soccer",
    "MLB": "mlb",
    "NBA": "nba",
    "CBB": "cbb",
}

SPORT_TO_FETCH_ARG = {
    "NHL": "NHL",
    "SOCCER": "Soccer",
    "MLB": "MLB",
    "NBA": "NBA",
    "CBB": "CBB",
}


def _expand_dates(start: str, end: str) -> list[str]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    if e < s:
        return []
    out = []
    cur = s
    while cur <= e:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _slate_path(sport_slug: str, date_str: str) -> Path | None:
    candidates = [
        REPO / "outputs" / date_str / f"step8_{sport_slug}_direction_clean_{date_str}.xlsx",
        REPO / "outputs" / date_str / f"step8_{sport_slug}_direction_clean.xlsx",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _run(cmd: list[str], label: str) -> bool:
    print(f"  [{label}] {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print(f"  [{label}] TIMEOUT after 600s")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-10:]
        print(f"  [{label}] FAILED rc={proc.returncode}")
        for line in tail:
            print(f"      {line}")
        return False
    return True


def _grade_for_date(sport: str, date_str: str) -> bool:
    sport_slug = SPORT_TO_SLUG[sport]
    actuals_path = REPO / "outputs" / date_str / f"actuals_{sport_slug}_{date_str}.csv"
    if not actuals_path.is_file():
        print(f"  [{sport} {date_str}] missing actuals after fetch — skipping grade")
        return False
    slate = _slate_path(sport_slug, date_str)
    if slate is None:
        print(f"  [{sport} {date_str}] no slate xlsx — skipping grade")
        return False
    out_dir = REPO / "outputs" / date_str
    cmd = [
        PY,
        str(REPO / "scripts" / "nhl_soccer_grader.py"),
        "--sport",
        SPORT_TO_FETCH_ARG[sport],
        "--date",
        date_str,
        "--slate",
        str(slate),
        "--actuals",
        str(actuals_path),
        "--output-dir",
        str(out_dir),
    ]
    return _run(cmd, f"grade {sport} {date_str}")


def _fetch_for_date(sport: str, date_str: str) -> bool:
    sport_slug = SPORT_TO_SLUG[sport]
    out_path = REPO / "outputs" / date_str / f"actuals_{sport_slug}_{date_str}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backup = out_path.with_suffix(".csv.bak")
    if out_path.is_file() and not backup.is_file():
        shutil.copy2(out_path, backup)
    cmd = [
        PY,
        str(REPO / "scripts" / "fetch_actuals.py"),
        "--sport",
        SPORT_TO_FETCH_ARG[sport],
        "--date",
        date_str,
        "--output",
        str(out_path),
    ]
    return _run(cmd, f"fetch {sport} {date_str}")


def _refresh_json_for_date(date_str: str) -> bool:
    cmd = [
        PY,
        str(REPO / "scripts" / "backfill_graded_props_json.py"),
        "--date",
        date_str,
    ]
    if not _run(cmd, f"json {date_str}"):
        return False
    src = REPO / "ui_runner" / "templates" / f"graded_props_{date_str}.json"
    dst = REPO / "mobile" / "www" / f"graded_props_{date_str}.json"
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def _audit_for_date(date_str: str) -> None:
    cmd = [PY, str(REPO / "scripts" / "grade_quality_audit.py"), "--date", date_str]
    _run(cmd, f"audit {date_str}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sport",
        required=True,
        choices=sorted(SPORT_TO_SLUG.keys()),
    )
    ap.add_argument("--start", help="YYYY-MM-DD inclusive")
    ap.add_argument("--end", help="YYYY-MM-DD inclusive")
    ap.add_argument("--dates", nargs="*", help="Explicit list of YYYY-MM-DD")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--skip-grade", action="store_true")
    ap.add_argument("--skip-json", action="store_true")
    ap.add_argument("--no-audit", action="store_true")
    args = ap.parse_args()

    if args.dates:
        dates = sorted({d.strip() for d in args.dates if d.strip()})
    elif args.start and args.end:
        dates = _expand_dates(args.start, args.end)
    else:
        ap.error("Provide either --dates or both --start and --end")
        return 2

    print(f"Backfill {args.sport}: {len(dates)} date(s) ({dates[0]} -> {dates[-1]})")
    failures: list[str] = []
    for d in dates:
        print(f"\n=== {args.sport} {d} ===")
        if not args.skip_fetch and not _fetch_for_date(args.sport, d):
            failures.append(f"fetch:{d}")
            continue
        if not args.skip_grade and not _grade_for_date(args.sport, d):
            failures.append(f"grade:{d}")
            continue
        if not args.skip_json:
            _refresh_json_for_date(d)
        if not args.no_audit:
            _audit_for_date(d)

    print(f"\nDone. Failures: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
