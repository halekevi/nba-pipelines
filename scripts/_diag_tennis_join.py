#!/usr/bin/env python3
"""One-off Tennis step8 join diagnostic (paste output to chat)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
from build_retrain_dataset import (  # noqa: E402
    _prepare_step8,
    load_step8_dated_snapshot,
    load_step8_sport,
    normalize_direction,
    normalize_line,
    normalize_pick_type,
    player_join_key,
    prop_join_key,
)

df = pd.read_csv(_ROOT / "data/retrain_dataset.csv", encoding="utf-8-sig", low_memory=False)
tennis = df[df["sport"] == "Tennis"]
joined = tennis[tennis["rank_score"].notna()]
unjoined = tennis[tennis["rank_score"].isna()]

print(f"Tennis rows: {len(tennis):,}  joined: {len(joined):,} ({100 * len(joined) / max(len(tennis), 1):.1f}%)")

print(
    "Tennis join key columns:",
    [
        c
        for c in tennis.columns
        if c
        in [
            "player",
            "player_name",
            "name",
            "prop",
            "prop_type",
            "pick_type",
            "file_date",
            "game_date",
            "date",
            "line",
            "direction",
            "team",
            "opponent",
        ]
    ],
)

print("\nJoined sample (5 rows):")
print(joined[["player", "prop", "pick_type", "file_date", "line"]].head(5).to_string())

print("\nUnjoined sample (5 rows):")
print(unjoined[["player", "prop", "pick_type", "file_date", "line"]].head(5).to_string())

p = _ROOT / "Sports/Tennis/step8_tennis_direction_clean.xlsx"
if p.exists():
    s8 = pd.read_excel(p, engine="openpyxl")
    print("\nStep8 fallback columns:", list(s8.columns))
    print("\nStep8 fallback sample (5 rows):")
    cols = (
        ["player", "prop_type", "pick_type", "line"]
        if "player" in s8.columns
        else ["Player", "Prop", "Pick Type", "Line"]
    )
    print(s8[cols].head(5).to_string())

print("\n=== Join keys in build_retrain_dataset.py ===")
print("Graded → _n_player, _n_prop, _n_line, _n_pick, _n_dir")
print("Step8 (_prepare_step8): player←Player, prop←Prop|prop_type, pick_type←Pick Type, line, direction")
print("Tennis merge_on: [_n_player, _n_prop, _n_line, _n_pick, _n_dir]  (full 5-key, not NHL loose)")

print("\n--- load_step8_sport('Tennis') ---")
raw_sport = load_step8_sport(_ROOT, "Tennis")
print("rows:", len(raw_sport) if raw_sport is not None else None)
if raw_sport is not None:
    prep_s = _prepare_step8(raw_sport, "2026-05-09")
    print("prepared _n_prop unique:", sorted(prep_s["_n_prop"].unique())[:12])

print("\n--- load_step8_dated_snapshot('Tennis', '2026-05-11') ---")
raw, used_static = load_step8_dated_snapshot(_ROOT, "Tennis", "2026-05-11")
print("rows:", len(raw) if raw is not None else None, "used_static_fallback:", used_static)
if raw is not None:
    prep = _prepare_step8(raw, "2026-05-11")
    print("prepared sample (5):")
    print(prep[["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir"]].head(5).to_string())

dates = sorted(tennis["file_date"].astype(str).str[:10].unique())
print(f"\nTennis file_dates: {len(dates)}")
for d in dates:
    sub = _ROOT / "outputs" / d / "tennis"
    ex = sub.is_dir()
    sf = any(sub.glob("step8*")) if ex else False
    raw_d, static_d = load_step8_dated_snapshot(_ROOT, "Tennis", d)
    sub_df = tennis[tennis["file_date"].astype(str).str[:10] == d]
    j = int(sub_df["rank_score"].notna().sum())
    print(
        f"  {d}: outputs/tennis={ex} step8_file={sf} "
        f"loader_rows={len(raw_d) if raw_d is not None else 0} static_fallback={static_d} "
        f"graded={len(sub_df)} joined={j}"
    )

print("\n--- Row-level mismatch sample (2026-05-09 unjoined) ---")
raw09, _ = load_step8_dated_snapshot(_ROOT, "Tennis", "2026-05-09")
s8_09 = _prepare_step8(raw09, "2026-05-09")
g = unjoined[unjoined["file_date"].astype(str).str[:10] == "2026-05-09"].head(6)
for _, row in g.iterrows():
    nk = (
        player_join_key(row["player"]),
        prop_join_key(row["prop"]),
        normalize_line(row["line"]),
        normalize_pick_type(row["pick_type"]),
        normalize_direction(row["direction"]),
    )
    hits = s8_09[(s8_09["_n_player"] == nk[0]) & (s8_09["_n_prop"] == nk[1])]
    print(f"\nGraded: {row['player']} | {row['prop']} | line={row['line']} | {row['direction']} | pick={row['pick_type']}")
    print(f"  keys: player={nk[0]!r} prop={nk[1]!r} line={nk[2]!r} pick={nk[3]!r} dir={nk[4]!r}")
    print(f"  step8 same player+prop: {len(hits)} rows")
    if len(hits):
        show = ["_n_line", "_n_pick", "_n_dir"]
        if "rank_score" in hits.columns:
            show.append("rank_score")
        print(hits[show].head(8).to_string())
