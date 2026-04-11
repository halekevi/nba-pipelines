#!/usr/bin/env python3
"""
Tennis step4 — ESPN match history (stat_g1..10) + optional season stats from /statistics.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from tennis_shared import (
    history_value_key,
    load_match_games_cache,
    parse_tennis_season_stats,
    refresh_match_games_cache,
    fetch_athlete_statistics,
)


def main() -> None:
    print("[Tennis step4] Starting...")
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step3_tennis_with_defense.csv")
    ap.add_argument("--output", default="outputs/step4_tennis_with_stats.csv")
    ap.add_argument("--match-cache", default="cache/tennis_match_games.json")
    ap.add_argument("--stats-cache", default="data/tennis_stats_cache.csv")
    ap.add_argument("--refresh-cache", action="store_true")
    ap.add_argument("--fetch-espn-stats", action="store_true", help="Fetch /statistics per player (slow)")
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
    stat_path = Path(args.stats_cache)
    if not stat_path.is_absolute():
        stat_path = root / stat_path

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        print("ERROR [Tennis step4] empty input")
        sys.exit(1)

    if args.refresh_cache or not mpath.is_file():
        print("[Tennis step4] Refreshing match games cache...")
        cache = refresh_match_games_cache(mpath)
    else:
        cache = load_match_games_cache(mpath)

    stat_cache: dict[tuple[str, str], dict[str, float | None]] = {}

    def _parse_float_cell(series: pd.Series, col: str) -> float | None:
        if col not in series.index:
            return None
        v = str(series.get(col, "")).strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    if stat_path.is_file():
        try:
            sc = pd.read_csv(stat_path, dtype=str, encoding="utf-8-sig").fillna("")
            for _, r in sc.iterrows():
                aid = str(r.get("espn_athlete_id", "")).strip()
                tour = str(r.get("tour", "ATP")).strip().upper() or "ATP"
                if not aid:
                    continue
                stat_cache[(aid, tour)] = {
                    "aces_per_match": _parse_float_cell(r, "aces_per_match"),
                    "double_faults_per_match": _parse_float_cell(r, "double_faults_per_match"),
                    "first_serve_pct": _parse_float_cell(r, "first_serve_pct"),
                    "games_won_per_match": _parse_float_cell(r, "games_won_per_match"),
                    "sets_won_per_match": _parse_float_cell(r, "sets_won_per_match"),
                    "win_rate_L10": _parse_float_cell(r, "win_rate_L10"),
                }
        except Exception as e:
            print(f"  [WARN] stats cache read failed: {e}")

    df["stat_status"] = "PENDING"
    for c in (
        "aces_per_match",
        "double_faults_per_match",
        "first_serve_pct",
        "games_won_per_match",
        "sets_won_per_match",
        "win_rate_L10",
        "best_surface",
    ):
        if c not in df.columns:
            df[c] = np.nan

    hkeys = [history_value_key(str(x)) or "" for x in df["prop_norm"].tolist()]

    for gi in range(1, 11):
        df[f"stat_g{gi}"] = np.nan

    if args.fetch_espn_stats:
        seen: set[tuple[str, str]] = set()
        for _, r in df.iterrows():
            aid = str(r.get("espn_athlete_id", "")).strip()
            tour = str(r.get("tour", "ATP")).strip().upper() or "ATP"
            if not aid or (aid, tour) in seen:
                continue
            seen.add((aid, tour))
            key = (aid, tour)
            if key in stat_cache and stat_cache[key].get("aces_per_match") is not None:
                continue
            try:
                time.sleep(0.35)
                payload = fetch_athlete_statistics(tour, aid)
                parsed = parse_tennis_season_stats(payload)
                if not parsed or all(v is None for v in parsed.values()):
                    print(f"  [WARN] ESPN statistics empty for athlete_id={aid} tour={tour}")
                wr = None
                hist = cache.get(aid) or []
                if hist:
                    wins = sum(1 for m in hist[:10] if float(m.get("games_won") or 0) >= 12.0)
                    wr = wins / min(10, len(hist))
                stat_cache[key] = {
                    "aces_per_match": parsed.get("aces_per_match"),
                    "double_faults_per_match": parsed.get("double_faults_per_match"),
                    "first_serve_pct": parsed.get("first_serve_pct"),
                    "games_won_per_match": parsed.get("games_won_per_match"),
                    "sets_won_per_match": parsed.get("sets_won_per_match"),
                    "win_rate_L10": wr,
                }
            except Exception as e:
                print(f"  [WARN] stats fetch failed {aid}: {e}")

        stat_path.parent.mkdir(parents=True, exist_ok=True)
        rows_out: list[dict[str, object]] = []
        for (aid, tour), vals in stat_cache.items():
            row = {"espn_athlete_id": aid, "tour": tour}
            for k, v in vals.items():
                row[k] = "" if v is None else v
            rows_out.append(row)
        if rows_out:
            pd.DataFrame(rows_out).to_csv(stat_path, index=False, encoding="utf-8-sig")

    for pos in range(len(df)):
        r = df.iloc[pos]
        aid = str(r.get("espn_athlete_id", "")).strip()
        tour = str(r.get("tour", "ATP")).strip().upper() or "ATP"
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
        else:
            df.iat[pos, df.columns.get_loc("stat_status")] = "OK"
            for j, v in enumerate(vals[:10]):
                df.iat[pos, df.columns.get_loc(f"stat_g{j + 1}")] = v

        st = stat_cache.get((aid, tour))
        if st:
            for k, v in st.items():
                if k in df.columns and v is not None:
                    df.iat[pos, df.columns.get_loc(k)] = v

    gcols = [f"stat_g{i}" for i in range(1, 11)]
    sub = df[gcols].apply(pd.to_numeric, errors="coerce")
    df["stat_last5_avg"] = sub.iloc[:, :5].mean(axis=1)
    df["stat_last10_avg"] = sub.mean(axis=1)
    df["stat_season_avg"] = df["stat_last10_avg"]

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    ok_n = int((df["stat_status"] == "OK").sum())
    print(f"OK [Tennis step4] -> {out}  rows={len(df)}  stat_OK={ok_n}")


if __name__ == "__main__":
    main()
