#!/usr/bin/env python3
# NFL SCAFFOLD — inactive until September 2026

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

SPORT = "NFL"
SPORT_KEY = "nfl"
ESPN_SPORT_PATH = "/sports/football/nfl/"
ODDS_API_SPORT_KEY = "americanfootball_nfl"
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

# TODO: nfl table not yet in proporacle_ref.db
DB_TABLE_NAME = "nfl_player_stats"


def main() -> int:
    ap = argparse.ArgumentParser(description="NFL step4 scaffold")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    df = pd.read_csv(args.input) if Path(args.input).is_file() else pd.DataFrame()
    for col in ("l5_avg", "l10_avg"):
        if col not in df.columns:
            df[col] = pd.NA
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[NFL step4] rows={len(df)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
