#!/usr/bin/env python3
"""Verify WNBA step4 rolling stats include all games (no minutes gate) for full slate."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WNBA = ROOT / "Sports" / "WNBA"
sys.path.insert(0, str(WNBA))
import step4_fetch_player_stats as s4  # noqa: E402


def _vals_close(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return round(float(a), 6) == round(float(b), 6)


def main() -> int:
    slate = pd.read_csv(WNBA / "step4_wnba_stats.csv", dtype=str).fillna("")
    cache = pd.read_csv(WNBA / "wnba_espn_cache.csv", dtype=str).fillna("")

    stat_target = datetime(2026, 5, 27)
    merged = {"2025", "2026"}
    lookback = 420
    cutoff = stat_target - timedelta(days=lookback)
    cache_dates = pd.to_datetime(cache["game_date"], errors="coerce")
    cache_filt = cache[
        cache["SEASON"].astype(str).isin(merged)
        & (cache_dates >= pd.Timestamp(cutoff))
        & (cache_dates <= pd.Timestamp(stat_target))
    ].copy()
    cache_filt = cache_filt.sort_values("game_date", ascending=False)

    if "PLAYER_NORM" not in cache_filt.columns:
        cache_filt["PLAYER_NORM"] = cache_filt["PLAYER_NAME"].map(s4._norm_name)

    name_to_id = (
        cache_filt.drop_duplicates("PLAYER_NORM")
        .set_index("PLAYER_NORM")["ESPN_ATHLETE_ID"]
        .to_dict()
    )

    def rolling_vals(player: str, prop_norm: str, min_minutes: float):
        p_norm = s4._norm_name(player)
        ath_id = name_to_id.get(p_norm, "")
        if not ath_id:
            return None, "NO_ESPN_ID"
        pg = cache_filt[cache_filt["ESPN_ATHLETE_ID"].astype(str) == str(ath_id)].copy()
        if pg.empty:
            return None, "NO_CACHE_GAMES"
        pg = pg.sort_values("game_date", ascending=False)
        pg = s4.filter_games_by_minutes(pg, float(min_minutes))
        stat_series = s4.derive_stat(pg, prop_norm)
        vals = [float(v) if pd.notna(v) else np.nan for v in stat_series.tolist()][:5]
        if not vals or all(isinstance(v, float) and np.isnan(v) for v in vals):
            return None, "UNSUPPORTED"
        return vals, "OK"

    rows = []
    for _, row in slate.iterrows():
        player = str(row.get("player", "")).strip()
        prop = s4.resolve_prop_slug(row)
        if not player or not prop:
            continue
        actual = [pd.to_numeric(row.get(f"stat_g{i}"), errors="coerce") for i in range(1, 6)]
        v0, st0 = rolling_vals(player, prop, 0.0)
        v20, st20 = rolling_vals(player, prop, 20.0)
        match0 = (
            st0 == "OK"
            and len(actual) == 5
            and all(_vals_close(a, b) for a, b in zip(actual, v0))
        )
        changed_vs_old = (
            st0 == "OK"
            and st20 == "OK"
            and v0 is not None
            and v20 is not None
            and [
                round(x, 6) if pd.notna(x) else None for x in v0
            ]
            != [round(x, 6) if pd.notna(x) else None for x in v20]
        )
        rows.append(
            {
                "player": player,
                "prop": prop,
                "status": st0,
                "match_no_filter": match0,
                "changed_vs_min20": changed_vs_old,
                "actual_g1": actual[0] if actual else np.nan,
                "new_g1": v0[0] if v0 else np.nan,
                "old_g1": v20[0] if v20 else np.nan,
                "line": pd.to_numeric(row.get("line"), errors="coerce"),
            }
        )

    rep = pd.DataFrame(rows)
    out_csv = ROOT / "data" / "reports" / "wnba_step4_rolling_verify.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(out_csv, index=False)

    print("=== WNBA step4 full-slate verification ===")
    print(f"Slate prop rows: {len(slate)}")
    print(f"Unique players: {slate['player'].nunique()}")
    print()
    print("Recompute status:")
    print(rep["status"].value_counts().to_string())
    print()
    ok = rep[rep["status"] == "OK"]
    print(
        f"stat_g1..g5 match cache (min filter=0): {int(ok['match_no_filter'].sum())} / {len(ok)} OK rows"
    )
    mismatch = ok[~ok["match_no_filter"]]
    print(f"Mismatches (should be 0): {len(mismatch)}")
    if len(mismatch):
        print(mismatch[["player", "prop", "actual_g1", "new_g1"]].head(20).to_string(index=False))
    print()
    print(
        f"Rows where L5 differs vs old min-minutes=20: {int(rep['changed_vs_min20'].sum())} / {len(rep)}"
    )
    chg = rep[rep["changed_vs_min20"]].copy()
    if len(chg):
        chg["g1_shift"] = chg.apply(
            lambda r: f"{r['old_g1']} -> {r['new_g1']}", axis=1
        )
        print(chg[["player", "prop", "g1_shift"]].to_string(index=False))
    print()
    low_min_players: set[str] = set()
    for player in slate["player"].unique():
        p_norm = s4._norm_name(str(player))
        ath_id = name_to_id.get(p_norm, "")
        if not ath_id:
            continue
        pg = cache_filt[
            cache_filt["ESPN_ATHLETE_ID"].astype(str) == str(ath_id)
        ].sort_values("game_date", ascending=False)
        if pg.empty:
            continue
        mins = s4._minutes_series(pg).head(5)
        if (mins < 15).any():
            low_min_players.add(str(player))
    on_slate = set(slate["player"].astype(str))
    affected = low_min_players & on_slate
    print(
        f"Players on slate with sub-15-min game in 5 most recent cache outings: {len(affected)}"
    )
    if affected:
        print(" ", ", ".join(sorted(affected)[:25]), ("..." if len(affected) > 25 else ""))
    print()
    print(f"Full report -> {out_csv}")
    return 1 if len(mismatch) else 0


if __name__ == "__main__":
    raise SystemExit(main())
