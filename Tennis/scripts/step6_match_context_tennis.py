#!/usr/bin/env python3
"""
Tennis step6 — surface / match tier labels + L5 over/under counts for step8 tie-breaks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from tennis_shared import load_or_refresh_rankings, norm_key


def infer_surface(row: pd.Series) -> str:
    # PrizePicks rarely sends surface; optional expansion from tournament field.
    st = str(row.get("start_time", "")) + " " + str(row.get("prop_type", ""))
    st = st.lower()
    if "clay" in st:
        return "CLAY"
    if "grass" in st or "wimbledon" in st:
        return "GRASS"
    return "HARD"


def main() -> None:
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step5_tennis_hit_rates.csv")
    ap.add_argument("--output", default="outputs/step6_tennis_role_context.csv")
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
        print("ERROR [Tennis-S6] empty input")
        sys.exit(1)

    df["surface"] = [infer_surface(df.iloc[i]) for i in range(len(df))]

    rpath = Path(args.rankings_cache)
    if not rpath.is_absolute():
        rpath = root / rpath
    rankings = load_or_refresh_rankings(rpath)
    by_id = {str(r["espn_athlete_id"]): float(r.get("rank") or 999) for r in rankings}
    by_pk = {str(r.get("player_key") or ""): float(r.get("rank") or 999) for r in rankings if r.get("player_key")}
    pranks = []
    for _, r in df.iterrows():
        aid = str(r.get("espn_athlete_id", "")).strip()
        if aid and aid in by_id:
            pranks.append(by_id[aid])
        else:
            pk = norm_key(str(r.get("player", "")))
            pranks.append(by_pk.get(pk, 999.0))
    df["player_atp_rank"] = pranks

    def match_tier(rk: float) -> str:
        if rk <= 20:
            return "M1000_LEVEL"
        if rk <= 80:
            return "ATP250_500"
        return "LOW_VISIBILITY"

    df["match_tier"] = [match_tier(float(x)) for x in df["player_atp_rank"]]

    df["position_group"] = df["tour"].astype(str).str.upper()
    df["minutes_tier"] = 2
    df["shot_role"] = np.where(df["prop_norm"].astype(str).str.contains("ace"), "SERVE_HEAVY", "NEUTRAL")
    df["usage_role"] = np.where(df["player_atp_rank"].astype(float) <= 32, "ELITE", "FIELD")

    o5 = pd.to_numeric(df.get("line_hits_over_5", np.nan), errors="coerce")
    u5 = pd.to_numeric(df.get("line_hits_under_5", np.nan), errors="coerce")
    df["last5_over"] = o5
    df["last5_under"] = u5

    df["game_script_mult"] = 1.0
    df["game_script_note"] = ""
    df["avg_minutes"] = np.nan

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"OK [Tennis-S6] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
