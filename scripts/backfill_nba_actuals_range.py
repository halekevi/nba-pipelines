#!/usr/bin/env python3
"""
Re-fetch NBA actuals for each calendar day in [start, end] (inclusive).

Writes the same layout as run_grader.ps1 expects:
  outputs/<YYYY-MM-DD>/actuals_nba_<YYYY-MM-DD>.csv
  (injuries sidecar is written by fetch_actuals next to that path)

Usage:
  py -3 scripts/backfill_nba_actuals_range.py --start 2026-04-01 --end 2026-04-30
  py -3 scripts/backfill_nba_actuals_range.py --start 2026-05-05 --nba-window 1
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _parse_iso(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill NBA actuals CSVs for a date range.")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", default="", help="YYYY-MM-DD inclusive (default: same as --start)")
    ap.add_argument("--nba-window", type=int, default=1, help="Passed to fetch_actuals (default: 1)")
    ap.add_argument("--sleep", type=float, default=0.35, help="Seconds between days (default: 0.35)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands only",
    )
    args = ap.parse_args()

    d0 = _parse_iso(args.start)
    d1 = _parse_iso(args.end) if str(args.end).strip() else d0
    if d1 < d0:
        raise SystemExit("--end must be on or after --start")

    fetch_script = _REPO / "scripts" / "fetch_actuals.py"
    if not fetch_script.is_file():
        raise SystemExit(f"Missing {fetch_script}")

    cur = d0
    n_ok = n_fail = 0
    while cur <= d1:
        ds = cur.isoformat()
        out_dir = _REPO / "outputs" / ds
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / f"actuals_nba_{ds}.csv"
        cmd = [
            sys.executable,
            str(fetch_script),
            "--sport",
            "NBA",
            "--date",
            ds,
            "--nba-window",
            str(max(0, int(args.nba_window))),
            "--output",
            str(out_csv),
        ]
        print(" ".join(cmd))
        if not args.dry_run:
            r = subprocess.run(cmd, cwd=str(_REPO))
            if r.returncode == 0:
                n_ok += 1
            else:
                n_fail += 1
                print(f"  WARN: exit {r.returncode} for {ds}", file=sys.stderr)
            time.sleep(max(0.0, float(args.sleep)))
        cur += timedelta(days=1)

    if not args.dry_run:
        print(f"Done. OK={n_ok} fail={n_fail}")


if __name__ == "__main__":
    main()
