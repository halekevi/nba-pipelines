#!/usr/bin/env python3
"""
verify_enrichment_ready.py — cache key counts + retrain_dataset fill rates.

Run after step4b --refresh and build_retrain_dataset.py, before train_edge_model.py.

  py -3.14 scripts/verify_enrichment_ready.py
  py -3.14 scripts/verify_enrichment_ready.py --smoke-test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

CACHE_PATHS = {
    "NBA usage": _REPO / "Sports/NBA/data/nba_usage_cache.json",
    "NBA pace": _REPO / "Sports/NBA/data/nba_team_pace_cache.json",
    "WNBA usage": _REPO / "Sports/WNBA/data/wnba_usage_cache.json",
    "WNBA pace": _REPO / "Sports/WNBA/data/wnba_team_pace_cache.json",
    "MLB pitcher": _REPO / "Sports/MLB/data/mlb_pitcher_splits_cache.json",
}

ENRICHMENT_CHECK_COLS = [
    "usage_pct",
    "team_pace",
    "batting_order_pos",
    "opp_pitcher_era_vs_batter_hand",
    "park_factor_overall",
    "wind_speed_mph",
    "role_stability_score",
    "star_tier",
]

MIN_KEYS = {
    "NBA usage": 50,
    "NBA pace": 25,
    "WNBA usage": 50,
    "WNBA pace": 10,
    "MLB pitcher": 25,
}


def _count_cache_keys(data: dict) -> int:
    n = 0
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        if "players" in v:
            n += len(v["players"])
        elif "teams" in v:
            n += len(v["teams"])
        elif "entries" in v:
            n += len(v["entries"])
        else:
            n += len(v)
    return n


def check_caches() -> bool:
    print("\n=== Cache key counts ===")
    ok_all = True
    for label, path in CACHE_PATHS.items():
        if not path.exists():
            print(f"MISSING   {label}: {path}")
            ok_all = False
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            keys = _count_cache_keys(data)
        except Exception as exc:
            print(f"ERROR     {label}: {exc}")
            ok_all = False
            continue
        need = MIN_KEYS.get(label, 50)
        status = "OK" if keys >= need else "LOW"
        if status != "OK":
            ok_all = False
        print(f"{status:8} {label}: {keys} keys (need >={need})")
    return ok_all


def check_retrain_csv(path: Path, nrows: int) -> bool:
    import pandas as pd

    print(f"\n=== Retrain CSV fill rates ({path}) ===")
    if not path.is_file():
        print(f"MISSING {path}")
        return False
    df = pd.read_csv(path, nrows=nrows, low_memory=False)
    print(f"Rows sampled: {len(df):,}")
    ok_all = True
    print(f"{'Column':<35} {'Fill':>8}  Status")
    print("-" * 55)
    for c in ENRICHMENT_CHECK_COLS:
        if c not in df.columns:
            print(f"{c:<35} {'---':>8}  MISSING")
            ok_all = False
            continue
        fill = float(df[c].notna().mean())
        if fill > 0.10:
            status = "OK"
        elif fill > 0:
            status = "LOW"
        else:
            status = "EMPTY"
        if status == "MISSING":
            ok_all = False
        print(f"{c:<35} {fill:>7.1%}  {status}")
    return ok_all


def _nba_scripts_dir() -> Path:
    return _REPO / "Sports" / "NBA" / "scripts"


def smoke_test_nba() -> bool:
    print("\n=== NBA stats.nba.com smoke test ===")
    nba_dir = str(_nba_scripts_dir())
    if nba_dir not in sys.path:
        sys.path.insert(0, nba_dir)
    try:
        from nba_stats_api import fetch_player_advanced

        df = fetch_player_advanced("2024-25")
        rows = len(df)
        ok = rows >= 400
        print(f"{'200' if ok else 'FAIL'} {rows} rows")
        return ok
    except Exception as exc:
        print(f"FAIL: {exc}")
        return False


def smoke_test_wnba() -> bool:
    import requests
    from nba_api.stats.library.http import STATS_HEADERS

    print("\n=== WNBA stats.wnba.com smoke test ===")
    headers = dict(STATS_HEADERS)
    headers["Referer"] = "https://www.wnba.com/"
    headers["Origin"] = "https://www.wnba.com"
    try:
        r = requests.get(
            "https://stats.wnba.com/stats/leaguedashplayerstats",
            params={
                "Season": "2025",
                "SeasonType": "Regular Season",
                "PerMode": "PerGame",
                "MeasureType": "Advanced",
                "LeagueId": "10",
            },
            headers=headers,
            timeout=30,
        )
        rows = 0
        if r.status_code == 200:
            rows = len(r.json().get("resultSets", [{}])[0].get("rowSet", []))
        print(f"{r.status_code} {rows} rows")
        return r.status_code == 200 and rows >= 50
    except Exception as exc:
        print(f"FAIL: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke-test", action="store_true")
    ap.add_argument("--retrain-csv", default=str(_REPO / "data/retrain_dataset.csv"))
    ap.add_argument("--nrows", type=int, default=200_000)
    args = ap.parse_args()

    ok = True
    if args.smoke_test:
        ok = smoke_test_nba() and ok
        ok = smoke_test_wnba() and ok
    ok = check_caches() and ok
    ok = check_retrain_csv(Path(args.retrain_csv), args.nrows) and ok

    print("\n" + ("READY for retrain." if ok else "NOT READY — fix items above before train_edge_model.py."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
