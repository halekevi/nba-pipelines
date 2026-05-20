#!/usr/bin/env python3
"""
Re-grade NBA1Q / NBA1H rows in graded_props JSON using period actuals CSVs.

Fetches missing period actuals via fetch_nba_period_actuals.py when --fetch-missing.
Preserves original actual_value as actual_value_original on change.

Usage:
  py -3.14 scripts/grading/regrade_nba1q.py --date 2026-02-24
  py -3.14 scripts/grading/regrade_nba1q.py --all --fetch-missing
  py -3.14 scripts/grading/regrade_nba1q.py --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"
_GRADED_DIR = _REPO / "mobile" / "www"
_TEMPLATES_DIR = _REPO / "ui_runner" / "templates"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from grading.slate_grader import (  # noqa: E402
    _build_actuals_lookup,
    _resolve_actual,
    grade,
    norm_player_key,
    norm_prop_key,
)


def _actuals_path(date_str: str, sport: str) -> Path:
    seg = "1Q" if sport == "NBA1Q" else "1H"
    return _REPO / "outputs" / date_str / f"actuals_nba{seg.lower()}_{date_str}.csv"


def _fetch_actuals(date_str: str, sport: str) -> Path:
    out = _actuals_path(date_str, sport)
    out.parent.mkdir(parents=True, exist_ok=True)
    segment = "1Q" if sport == "NBA1Q" else "1H"
    cmd = [
        sys.executable,
        str(_SCRIPTS / "fetch_nba_period_actuals.py"),
        "--date",
        date_str,
        "--segment",
        segment,
        "--output",
        str(out),
    ]
    print(f"  [fetch] {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(_REPO))
    return out


def _load_lookup(date_str: str, sport: str, *, fetch_missing: bool) -> dict[str, float]:
    path = _actuals_path(date_str, sport)
    if not path.is_file():
        if not fetch_missing:
            return {}
        _fetch_actuals(date_str, sport)
    if not path.is_file():
        return {}
    act = pd.read_csv(path)
    return _build_actuals_lookup(act)


def _row_series(prop: dict) -> pd.Series:
    prop_raw = str(prop.get("prop", "") or "")
    return pd.Series(
        {
            "player": prop.get("player", ""),
            "team": prop.get("team", ""),
            "prop_type_norm": norm_prop_key(prop_raw),
            "prop_norm": norm_prop_key(prop_raw),
            "prop_type": prop_raw,
            "line": prop.get("line"),
            "bet_direction": str(prop.get("direction", "") or "OVER").upper(),
        }
    )


def _regrade_prop(prop: dict, act_map: dict[str, float]) -> bool:
    row = _row_series(prop)
    row["player_key"] = norm_player_key(row["player"]) + "|" + norm_prop_key(row["prop_type_norm"])
    new_actual = _resolve_actual(act_map, row)
    if pd.isna(new_actual):
        return False

    old_raw = prop.get("actual_value")
    try:
        old_actual = float(old_raw) if old_raw not in (None, "") else float("nan")
    except (TypeError, ValueError):
        old_actual = float("nan")

    new_actual_f = float(new_actual)
    if pd.notna(old_actual) and abs(old_actual - new_actual_f) < 1e-9:
        prop.pop("grading_suspect", None)
        prop.pop("grading_reason", None)
        return False

    if pd.notna(old_actual) and "actual_value_original" not in prop:
        prop["actual_value_original"] = old_actual

    prop["actual_value"] = new_actual_f
    result, void_reason, margin = grade(row, new_actual_f)
    prop["result"] = result
    if void_reason:
        prop["void_reason"] = void_reason
    elif "void_reason" in prop and str(prop.get("void_reason", "")).upper() == "NO_ACTUAL":
        prop["void_reason"] = ""
    if pd.notna(margin):
        prop["margin"] = margin
    prop["grading_corrected"] = True
    prop.pop("grading_suspect", None)
    prop.pop("grading_reason", None)
    return True


def regrade_file(path: Path, *, fetch_missing: bool, dry_run: bool) -> tuple[int, int]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    date_str = str(raw.get("date") or path.stem.replace("graded_props_", ""))[:10]
    props = raw.get("props", [])
    if not isinstance(props, list):
        return 0, 0

    lookups: dict[str, dict[str, float]] = {}
    changed = 0
    touched = 0
    for p in props:
        if not isinstance(p, dict):
            continue
        sport = str(p.get("sport", "")).strip().upper()
        if sport not in ("NBA1Q", "NBA1H"):
            continue
        touched += 1
        if sport not in lookups:
            lookups[sport] = _load_lookup(date_str, sport, fetch_missing=fetch_missing)
        if _regrade_prop(p, lookups[sport]):
            changed += 1

    if changed and not dry_run:
        text = json.dumps(raw, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8")
        twin = _TEMPLATES_DIR / path.name
        if twin.parent.is_dir():
            twin.write_text(text, encoding="utf-8")
    return touched, changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default="", help="Single YYYY-MM-DD file to regrade")
    ap.add_argument("--all", action="store_true", help="Regrade every graded_props_*.json")
    ap.add_argument("--fetch-missing", action="store_true", help="Fetch period actuals when CSV missing")
    ap.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    ap.add_argument("--dir", type=Path, default=_GRADED_DIR)
    args = ap.parse_args()

    if args.date:
        paths = [args.dir / f"graded_props_{args.date[:10]}.json"]
    elif args.all:
        paths = sorted(p for p in args.dir.glob("graded_props_*.json") if ".bak_" not in p.name)
    else:
        print("Specify --date YYYY-MM-DD or --all", file=sys.stderr)
        return 1

    total_changed = 0
    for path in paths:
        if not path.is_file():
            print(f"  skip missing {path.name}")
            continue
        touched, changed = regrade_file(path, fetch_missing=args.fetch_missing, dry_run=args.dry_run)
        if changed or touched:
            verb = "would change" if args.dry_run else "changed"
            print(f"  {path.name}: {verb} {changed}/{touched} period rows")
        total_changed += changed

    print(f"\nTotal rows re-graded: {total_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
