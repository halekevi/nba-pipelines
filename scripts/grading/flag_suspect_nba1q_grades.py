#!/usr/bin/env python3
"""
Flag NBA1Q/NBA1H graded_props rows likely graded with full-game stats.

Heuristic: period props with actual_value far above plausible Q1/H1 ranges.
Adds grading_suspect + grading_reason; does not delete or change hit/actual.

Usage:
  py -3.14 scripts/grading/flag_suspect_nba1q_grades.py
  py -3.14 scripts/grading/flag_suspect_nba1q_grades.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_GRADED_DIR = _REPO / "mobile" / "www"


def _prop_kind(prop: str) -> str:
    p = str(prop or "").strip().casefold()
    if "point" in p or p in ("pts",):
        return "points"
    if "rebound" in p or p in ("reb",):
        return "rebounds"
    if "assist" in p or p in ("ast",):
        return "assists"
    if "3-pt" in p or "3pt" in p or "fg3" in p:
        return "threes"
    return "other"


def is_suspect_row(sport: str, prop: str, actual: float) -> tuple[bool, str]:
    kind = _prop_kind(prop)
    if sport == "NBA1Q":
        limits = {
            "points": 20.0,
            "rebounds": 12.0,
            "assists": 10.0,
            "threes": 6.0,
            "other": 25.0,
        }
        reason = "full_game_stats_used_for_period_prop"
    elif sport == "NBA1H":
        limits = {
            "points": 35.0,
            "rebounds": 20.0,
            "assists": 18.0,
            "threes": 10.0,
            "other": 40.0,
        }
        reason = "full_game_stats_used_for_period_prop"
    else:
        return False, ""
    cap = limits.get(kind, limits["other"])
    if actual > cap:
        return True, reason
    return False, ""


def process_file(path: Path, *, dry_run: bool) -> tuple[int, int]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    props = raw.get("props", [])
    if not isinstance(props, list):
        return 0, 0
    flagged = 0
    for p in props:
        if not isinstance(p, dict):
            continue
        sport = str(p.get("sport", "")).strip().upper()
        if sport not in ("NBA1Q", "NBA1H"):
            continue
        try:
            actual = float(p.get("actual_value"))
        except (TypeError, ValueError):
            continue
        suspect, reason = is_suspect_row(sport, str(p.get("prop", "")), actual)
        if not suspect:
            if p.get("grading_suspect"):
                p.pop("grading_suspect", None)
                p.pop("grading_reason", None)
            continue
        flagged += 1
        p["grading_suspect"] = True
        p["grading_reason"] = reason
    if flagged and not dry_run:
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(props), flagged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print counts only; do not write JSON")
    ap.add_argument(
        "--dir",
        type=Path,
        default=_GRADED_DIR,
        help="Directory containing graded_props_*.json (default: mobile/www)",
    )
    args = ap.parse_args()
    paths = sorted(p for p in args.dir.glob("graded_props_*.json") if ".bak_" not in p.name)
    if not paths:
        print(f"No graded_props files under {args.dir}", file=sys.stderr)
        return 1

    total_flagged = 0
    total_period = 0
    for path in paths:
        n_props, n_flag = process_file(path, dry_run=args.dry_run)
        total_period += n_flag
        total_flagged += n_flag
        if n_flag:
            print(f"  {path.name}: flagged {n_flag}")

    mode = "would flag" if args.dry_run else "flagged"
    print(f"\n{mode} {total_flagged} NBA1Q/NBA1H rows across {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
