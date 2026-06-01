#!/usr/bin/env python3
"""
Refresh NFL / CFB ranking reference CSVs when missing or older than the weekly threshold.

  py scripts/refresh_rankings.py --sport all
  py scripts/refresh_rankings.py --sport nfl --force
  py scripts/refresh_rankings.py --sport cfb --season 2025
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_AGE_DAYS = 7

NFL_REFERENCE = REPO_ROOT / "data" / "reference" / "nfl_team_defense.csv"
CFB_REFERENCE = (
    REPO_ROOT / "Sports" / "CFB" / "data" / "reference" / "cfb_team_unit_rankings.csv"
)
CFB_ROOT = REPO_ROOT / "Sports" / "CFB"
NFL_PULL_SCRIPT = REPO_ROOT / "Sports" / "NFL" / "scripts" / "pull_nfl_defense_stats.py"
CFB_BUILD_SCRIPT = REPO_ROOT / "Sports" / "CFB" / "scripts" / "build_cfb_unit_rankings.py"


def file_age_days(path: Path) -> float | None:
    if not path.is_file():
        return None
    return (time.time() - path.stat().st_mtime) / 86400.0


def should_refresh(
    path: Path, *, force: bool, max_age_days: float = MAX_AGE_DAYS
) -> tuple[bool, float | None]:
    if force:
        return True, file_age_days(path)
    age = file_age_days(path)
    if age is None:
        return True, None
    return age > max_age_days, age


def _run_subprocess(cmd: list[str], label: str, *, cwd: Path | None = None) -> int:
    print(f"[{label}] refreshing...")
    proc = subprocess.run(cmd, cwd=str(cwd or REPO_ROOT), capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    if proc.returncode != 0:
        print(f"[{label}] WARN: exit {proc.returncode}", file=sys.stderr)
    return int(proc.returncode)


def refresh_nfl(*, force: bool = False, max_age_days: float = MAX_AGE_DAYS) -> int:
    label = "NFL"
    path = NFL_REFERENCE
    stale, age = should_refresh(path, force=force, max_age_days=max_age_days)
    if not stale:
        print(f"[{label}] rankings fresh (age: {age:.1f}d) — skip refresh")
        return 0
    if age is not None:
        print(f"[{label}] rankings stale (age: {age:.1f}d) — refresh")
    else:
        print(f"[{label}] rankings missing — refresh")

    if not NFL_PULL_SCRIPT.is_file():
        print(f"[{label}] ERROR: script not found: {NFL_PULL_SCRIPT}", file=sys.stderr)
        return 1
    return _run_subprocess([sys.executable, str(NFL_PULL_SCRIPT)], label)


def refresh_cfb(
    *, force: bool = False, season: int = 0, max_age_days: float = MAX_AGE_DAYS
) -> int:
    label = "CFB"
    path = CFB_REFERENCE
    stale, age = should_refresh(path, force=force, max_age_days=max_age_days)
    if not stale:
        print(f"[{label}] rankings fresh (age: {age:.1f}d) — skip refresh")
        return 0
    if age is not None:
        print(f"[{label}] rankings stale (age: {age:.1f}d) — refresh")
    else:
        print(f"[{label}] rankings missing — refresh")

    if not CFB_BUILD_SCRIPT.is_file():
        print(f"[{label}] ERROR: script not found: {CFB_BUILD_SCRIPT}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(CFB_BUILD_SCRIPT)]
    if season:
        cmd.extend(["--season", str(int(season))])
    cmd.extend(["--out", str(CFB_REFERENCE)])
    return _run_subprocess(cmd, label, cwd=CFB_ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh NFL/CFB ranking reference files if stale.")
    ap.add_argument("--sport", choices=("nfl", "cfb", "all"), default="all")
    ap.add_argument("--force", action="store_true", help="Refresh even if file is fresh.")
    ap.add_argument("--season", type=int, default=0, help="CFB ESPN season year (0 = auto).")
    ap.add_argument(
        "--max-age-days",
        type=float,
        default=MAX_AGE_DAYS,
        help=f"Stale threshold in days (default {MAX_AGE_DAYS}).",
    )
    args = ap.parse_args()
    max_age = float(args.max_age_days)

    exit_code = 0
    if args.sport in ("nfl", "all"):
        code = refresh_nfl(force=bool(args.force), max_age_days=max_age)
        exit_code = max(exit_code, code)
    if args.sport in ("cfb", "all"):
        code = refresh_cfb(
            force=bool(args.force), season=int(args.season), max_age_days=max_age
        )
        exit_code = max(exit_code, code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
