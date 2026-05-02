#!/usr/bin/env python3
"""
Tennis step2 — pick types, prop_norm, ESPN athlete id + tour from rankings (diacritic-safe keys).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from tennis_shared import load_or_refresh_rankings, norm_tennis_prop, resolve_athlete_id


def norm_pick(s: str) -> str:
    t = str(s or "").strip().lower()
    if "gob" in t:
        return "Goblin"
    if "dem" in t:
        return "Demon"
    return "Standard"


def main() -> None:
    print("[Tennis step2] Starting...")
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step1_tennis_props.csv")
    ap.add_argument("--output", default="outputs/step2_tennis_picktypes.csv")
    ap.add_argument("--rankings-cache", default="cache/tennis_rankings.json")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        print("ERROR [Tennis step2] empty step1")
        sys.exit(1)

    if "player" not in df.columns and "player_name" in df.columns:
        df["player"] = df["player_name"]
    if "line" not in df.columns and "line_score" in df.columns:
        df["line"] = df["line_score"]

    df["pick_type"] = df.get("pick_type", "Standard").map(norm_pick)
    df["prop_norm"] = df["prop_type"].map(norm_tennis_prop)

    supported = {
        "aces",
        "double_faults",
        "games_won",
        "sets_won",
        "match_total_games",
        "break_points_won",
    }
    df["unsupported_prop"] = (~df["prop_norm"].isin(supported)).astype(int)

    rpath = Path(args.rankings_cache)
    if not rpath.is_absolute():
        rpath = root / rpath
    rankings = load_or_refresh_rankings(rpath)

    eids: list[str] = []
    tours: list[str] = []
    for _, r in df.iterrows():
        eid, tour = resolve_athlete_id(str(r.get("player", "")), rankings)
        eids.append(eid)
        tours.append(tour or "ATP")

    df["espn_athlete_id"] = eids
    df["tour"] = tours

    df["start_time"] = df.get("start_time", "")

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"OK [Tennis step2] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
