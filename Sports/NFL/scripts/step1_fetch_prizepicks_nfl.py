#!/usr/bin/env python3
# NFL SCAFFOLD — inactive until September 2026

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SPORT = "NFL"
SPORT_KEY = "nfl"
LEAGUE_ID = "9"
BOARD_SIZE_MIN = 0
BOARD_TEAM_MIN = 0
NFL_PROP_NORMS = [
    "passing_yards",
    "rushing_yards",
    "receiving_yards",
    "passing_tds",
    "rushing_tds",
    "receptions",
    "completions",
    "kicking_points",
    "tackles_assists",
    "interceptions",
    "sacks",
    "fantasy_score",
]


def _empty_board() -> pd.DataFrame:
    cols = [
        "projection_id",
        "player_name",
        "team",
        "opp_team",
        "prop_type",
        "prop_norm",
        "line_score",
        "pick_type",
        "sport",
        "league_id",
        "game_date",
    ]
    return pd.DataFrame(columns=cols)


def main() -> int:
    ap = argparse.ArgumentParser(description="NFL step1 scaffold fetcher")
    ap.add_argument("--output", default="Sports/NFL/outputs/step1_nfl_props.csv")
    ap.add_argument("--date", default="")
    ap.add_argument("--league_id", default=LEAGUE_ID)
    args = ap.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = _empty_board()
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(
        f"[NFL step1] scaffold mode | league_id={args.league_id} | "
        f"rows={len(df)} teams=0 board_min={BOARD_SIZE_MIN}/{BOARD_TEAM_MIN}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
