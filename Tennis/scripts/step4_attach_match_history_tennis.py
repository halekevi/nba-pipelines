#!/usr/bin/env python3
"""
Tennis step4 — last matches games won / match total games from ESPN scoreboard cache.
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
from tennis_shared import history_value_key, load_match_games_cache, refresh_match_games_cache


def main() -> None:
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step3_tennis_with_context.csv")
    ap.add_argument("--output", default="outputs/step4_tennis_with_stats.csv")
    ap.add_argument("--match-cache", default="cache/tennis_match_games.json")
    ap.add_argument("--refresh-cache", action="store_true", help="Re-fetch scoreboards before attach")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out
    mpath = Path(args.match_cache)
    if not mpath.is_absolute():
        mpath = root / mpath

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        print("ERROR [Tennis-S4] empty input")
        sys.exit(1)

    if args.refresh_cache or not mpath.is_file():
        print("[Tennis-S4] refreshing match cache...")
        cache = refresh_match_games_cache(mpath)
    else:
        cache = load_match_games_cache(mpath)

    df["stat_status"] = "PENDING"

    hkeys = [history_value_key(str(x)) or "" for x in df["prop_norm"].tolist()]

    for gi in range(1, 11):
        df[f"stat_g{gi}"] = np.nan

    for pos in range(len(df)):
        r = df.iloc[pos]
        aid = str(r.get("espn_athlete_id", "")).strip()
        hk = hkeys[pos]
        unsup = int(float(r.get("unsupported_prop", 0) or 0))
        if unsup == 1 or not hk:
            df.iat[pos, df.columns.get_loc("stat_status")] = "UNSUPPORTED_PROP" if unsup == 1 else "NO_STAT_KEY"
            continue
        if not aid:
            df.iat[pos, df.columns.get_loc("stat_status")] = "NO_ID"
            continue
        hist = cache.get(aid) or []
        vals = []
        for m in hist:
            v = m.get(hk)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if not vals:
            df.iat[pos, df.columns.get_loc("stat_status")] = "NO_DATA"
            continue
        df.iat[pos, df.columns.get_loc("stat_status")] = "OK"
        for j, v in enumerate(vals[:10]):
            df.iat[pos, df.columns.get_loc(f"stat_g{j + 1}")] = v

    gcols = [f"stat_g{i}" for i in range(1, 11)]
    sub = df[gcols].apply(pd.to_numeric, errors="coerce")
    df["stat_last5_avg"] = sub.iloc[:, :5].mean(axis=1)
    df["stat_last10_avg"] = sub.mean(axis=1)
    df["stat_season_avg"] = df["stat_last10_avg"]

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    ok_n = int((df["stat_status"] == "OK").sum())
    print(f"OK [Tennis-S4] -> {out}  rows={len(df)}  stat_OK={ok_n}")


if __name__ == "__main__":
    main()
