#!/usr/bin/env python3
"""Direction × hit-rate breakdown for matchup slices (graded props)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "bt", _REPO / "scripts" / "backtest_player_tier_vs_defense.py"
)
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

TOUGH = frozenset({"Elite", "Above Avg"})
SOFT = frozenset({"Weak", "Below Avg"})


def _dir_table(sub: pd.DataFrame, label: str) -> None:
    if sub.empty:
        print(f"\n=== {label} — no rows ===")
        return
    print(f"\n=== {label} (n={len(sub):,}) ===")
    print("  (hit = bet won for stated direction: OVER hit = went over line, UNDER hit = stayed under)")
    for d in ("OVER", "UNDER"):
        g = sub[sub["direction"] == d]
        if g.empty:
            continue
        hr = float(g["hit"].mean())
        print(f"  {d:<6} HR={100*hr:.1f}%  n={len(g):,}  ({100 * len(g) / len(sub):.0f}% of slice)")
    print(f"  ALL    HR={100 * sub['hit'].mean():.1f}%")


def main() -> int:
    raw = bt.load_graded_with_defense(min_date="2026-05-06")
    raw["sport"] = raw["sport"].map(bt._norm_sport)
    raw["direction"] = raw["direction"].map(bt._norm_dir)
    raw["prop_type"] = raw["prop_type"].map(bt._norm_prop_type)
    raw["def_tier_norm"] = raw["def_tier"].map(
        lambda x: bt.normalize_def_tier_label(x) or "(missing)"
    )

    slate_dates = raw["file_date"].astype(str).tolist()
    lookups: dict = {}
    mlb_lu = bt._load_mlb_pit_lookup(slate_dates=slate_dates)
    if mlb_lu:
        lookups["MLB"] = mlb_lu
    nba_logs = bt._load_nba_logs()
    if not nba_logs.empty:
        lu = bt._build_pit_lookup_bball(
            nba_logs,
            slate_dates=slate_dates,
            categories=("pts", "reb", "ast", "stl", "blk", "fg3m", "pra"),
            team_key_fn=lambda t: bt._team_lookup_key("NBA", t),
        )
        for sp in ("NBA", "NBA1H", "NBA1Q"):
            lookups[sp] = lu
    wnba_logs = bt._load_wnba_logs()
    if not wnba_logs.empty:
        lookups["WNBA"] = bt._build_pit_lookup_bball(
            wnba_logs,
            slate_dates=slate_dates,
            categories=("pts", "reb", "ast", "stl", "blk", "fg3m", "pra"),
            team_key_fn=lambda t: bt._team_lookup_key("WNBA", t),
        )

    df = bt.attach_player_tier(raw, lookups)
    df = df[df["def_tier_norm"] != "(missing)"]

    print("=" * 72)
    print("  DIRECTION × HIT RATE BY MATCHUP SLICE (May 6–28 graded)")
    print("=" * 72)

    for sport, prop in [("MLB", "Hits"), ("NBA", "Points"), ("WNBA", "Points")]:
        sub = df[(df["sport"] == sport) & (df["prop_type"] == prop)]
        if sub.empty:
            continue
        bt_slice = sub[
            (sub["player_tier"] == "bottom") & sub["def_tier_norm"].isin(TOUGH)
        ]
        ts_slice = sub[(sub["player_tier"] == "top") & sub["def_tier_norm"].isin(SOFT)]
        _dir_table(bt_slice, f"{sport} {prop} | bottom-3 × tough DEF")
        _dir_table(ts_slice, f"{sport} {prop} | top-3 × soft DEF")
        _dir_table(sub, f"{sport} {prop} | all (baseline)")

    # Ticket legs: graded on_ticket flag
    rows: list[dict] = []
    for p in (_REPO / "mobile" / "www").glob("graded_props_2026-05-*.json"):
        fd = p.stem.replace("graded_props_", "")
        for r in json.loads(p.read_text(encoding="utf-8")).get("props", []):
            if str(r.get("sport", "")).upper() != "MLB":
                continue
            if bt._norm_prop_type(r.get("prop")) != "Hits":
                continue
            hit = bt._parse_hit(r.get("result"))
            if hit is None:
                continue
            rows.append(
                {
                    "player": str(r.get("player", "")).strip(),
                    "file_date": fd,
                    "direction": bt._norm_dir(r.get("direction")),
                    "def_tier": r.get("def_tier"),
                    "on_ticket": bool(r.get("on_ticket")),
                    "hit": int(hit),
                    "pick_type": str(r.get("pick_type", "")),
                }
            )
    if rows:
        tix = pd.DataFrame(rows)
        tix["def_tier_norm"] = tix["def_tier"].map(
            lambda x: bt.normalize_def_tier_label(x) or ""
        )
        meta = df[["player", "file_date", "player_tier"]].drop_duplicates()
        tix = tix.merge(meta, on=["player", "file_date"], how="left")
        on_t = tix[
            (tix["player_tier"] == "bottom")
            & (tix["def_tier_norm"].isin(TOUGH))
            & (tix["on_ticket"])
        ]
        _dir_table(on_t, "MLB Hits bottom×tough | on_ticket=True only")

    print("\n--- Sample: bottom-3 × tough DEF, MLB Hits (15 most recent) ---")
    mlb_hits = df[(df["sport"] == "MLB") & (df["prop_type"] == "Hits")]
    bt_h = mlb_hits[
        (mlb_hits["player_tier"] == "bottom") & mlb_hits["def_tier_norm"].isin(TOUGH)
    ]
    samp = bt_h.merge(
        raw[["player", "file_date", "team", "pick_type"]],
        on=["player", "file_date"],
        how="left",
    ).tail(15)
    for _, r in samp.iterrows():
        res = "HIT" if r["hit"] else "MISS"
        print(
            f"  {str(r['file_date'])[:10]}  {str(r['player'])[:24]:<24}  "
            f"{str(r.get('team', '')):<4}  {r['direction']:<5}  "
            f"{str(r.get('pick_type', ''))[:8]:<8}  def={r['def_tier_norm']:<10}  {res}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
