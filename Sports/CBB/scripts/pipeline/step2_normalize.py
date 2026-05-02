#!/usr/bin/env python3
"""
cbb_step2_normalize.py
-----------------------
Canonical normalization layer for CBB pipeline.
Takes raw pp_cbb_scraper.py output and guarantees a stable schema
for all downstream steps.

Adds:
- prop_norm          : canonical prop key (pts/reb/ast/pra/pr/pa/ra/stl/blk/stocks/tov/fantasy)
- pick_type          : normalized (Standard/Goblin/Demon)
- cbb_player_key     : espn_athlete_id if present, else player_norm|team_abbr
- team_abbr          : canonical team abbreviation (from pp_team)
- opp_team_abbr      : canonical opp abbreviation (from pp_opp_team)
- player_norm        : lowercased, alphanumeric only

Input : step1_fetch_prizepicks_api_cbb.csv
Output: step2_normalized_cbb.csv
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[4]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import log_pipeline_health

PROP_NORM_MAP = {
    # points
    "points": "pts",
    "pts": "pts",
    # rebounds
    "rebounds": "reb",
    "reb": "reb",
    # assists
    "assists": "ast",
    "ast": "ast",
    # combos
    "pts+rebs+asts": "pra",
    "pra": "pra",
    "pts+rebs": "pr",
    "pts+asts": "pa",
    "rebs+asts": "ra",
    # defensive
    "steals": "stl",
    "stl": "stl",
    "blocked shots": "blk",
    "blocks": "blk",
    "blk": "blk",
    "steals+blocks": "stocks",
    "blks+stls": "stocks",
    "stocks": "stocks",
    # turnovers
    "turnovers": "tov",
    "to": "tov",
    "tov": "tov",
    # 3-pointers  (canonical key = fg3m, matches step5b prop_value + step6 weight/prior tables)
    "3-pt made": "fg3m",
    "3 pt made": "fg3m",
    "3 pointers made": "fg3m",
    "threes made": "fg3m",
    "3pm": "fg3m",
    "fg3m": "fg3m",
    # fantasy
    "fantasy score": "fantasy",
    "fantasy": "fantasy",
}

TEAM_ALIASES = {
    "GCU": "GC", "NEVADA": "NEV", "SDST": "SDSU",
    "MIZ": "MIZZ", "NCSU": "NCST", "GTECH": "GT",
}


def norm_str(s: str) -> str:
    """Canonical player name normalizer.
    NFKD first so accented chars (é→e, ü→u, ô→o) map correctly before stripping,
    then lowercase + collapse non-alphanumeric to spaces.
    Strips name suffixes (Jr/Sr/II/III/IV) to match master builder.
    Matches the normalization used in build_ncaa_mbb_espn_athletes_master.py.
    """
    s = (s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)  # ← ADDED: strip suffixes
    s = re.sub(r"\s+", " ", s).strip()             # ← ADDED: collapse leftover spaces
    return s


def norm_team(s: str) -> str:
    t = str(s or "").strip().upper()
    return TEAM_ALIASES.get(t, t)


def norm_pick_type(x: str) -> str:
    t = str(x or "").lower().strip()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"


def norm_prop(s: str) -> str:
    p = str(s or "").lower().strip()
    p = re.sub(r"\s+", " ", p)
    return PROP_NORM_MAP.get(p, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", default="step2_normalized_cbb.csv")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.input, dtype=str).fillna("")
    except Exception as e:
        log_pipeline_health(
            "cbb.step2_normalize",
            "read_failed",
            extra={"input": args.input, "error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        raise

    print(f"→ Loaded: {args.input} | rows={len(df)}")

    # ── Bouncer (pre-normalization): reject junk rows early ───────────────────
    required_any = ["player", "pp_team", "line"]
    missing_any = [c for c in required_any if c not in df.columns]
    if missing_any:
        log_pipeline_health(
            "cbb.step2_normalize",
            "missing_required_columns",
            extra={"missing": missing_any, "cols": list(df.columns), "input": args.input},
            start=Path(__file__),
        )
        raise SystemExit(f"Missing required columns: {missing_any}")

    before_bounce = len(df)
    df = df[df["player"].astype(str).str.strip() != ""].copy()
    df = df[df["pp_team"].astype(str).str.strip() != ""].copy()
    # line must be numeric and non-negative for downstream ranking math
    line_num = pd.to_numeric(df["line"], errors="coerce")
    df = df[line_num.notna() & (line_num >= 0)].copy()
    bounced = before_bounce - len(df)
    if bounced:
        print(f"  🧹 Bouncer: removed {bounced} junk rows")
        log_pipeline_health(
            "cbb.step2_normalize",
            "bouncer_removed_rows",
            extra={"removed": bounced, "before": before_bounce, "after": len(df), "input": args.input},
            start=Path(__file__),
        )

    # player_norm
    player_col = "player" if "player" in df.columns else df.columns[0]
    df["player_norm"] = df[player_col].astype(str).apply(norm_str)

    # team_abbr / opp_team_abbr
    df["team_abbr"]     = df.get("pp_team",     pd.Series([""] * len(df))).astype(str).apply(norm_team)
    df["opp_team_abbr"] = df.get("pp_opp_team", pd.Series([""] * len(df))).astype(str).apply(norm_team)

    # prop_norm — prefer stat_type, fallback to prop_type
    stat_col = "stat_type" if "stat_type" in df.columns else ("prop_type" if "prop_type" in df.columns else "")
    if stat_col:
        df["prop_norm"] = df[stat_col].astype(str).apply(norm_prop)
    else:
        df["prop_norm"] = ""

    # pick_type normalized
    odds_col = "odds_type" if "odds_type" in df.columns else ("pick_type" if "pick_type" in df.columns else "")
    if odds_col:
        df["pick_type"] = df[odds_col].astype(str).apply(norm_pick_type)
    else:
        df["pick_type"] = "Standard"

    # cbb_player_key
    if "espn_athlete_id" in df.columns:
        df["cbb_player_key"] = df.apply(
            lambda r: str(r["espn_athlete_id"]).strip()
            if str(r.get("espn_athlete_id", "")).strip()
            else f"{r['player_norm']}|{r['team_abbr']}",
            axis=1,
        )
    else:
        df["cbb_player_key"] = df["player_norm"] + "|" + df["team_abbr"]

    # line numeric
    df["line"] = pd.to_numeric(df["line"], errors="coerce")

    # Rename prop column to prop_type for consistency downstream
    if "stat_type" in df.columns and "prop_type" not in df.columns:
        df["prop_type"] = df["stat_type"]

    # Guarantee column order: identity first, then prop details, then rest
    identity = ["proj_id", "player_id", "cbb_player_key", "player", "player_norm",
                "team_abbr", "opp_team_abbr", "pp_team", "pp_opp_team", "pos",
                "prop_type", "prop_norm", "pick_type", "line", "start_time",
                "pp_game_id", "league_id"]
    present  = [c for c in identity if c in df.columns]
    rest     = [c for c in df.columns if c not in present]
    df       = df[present + rest]

    df.to_csv(args.output, index=False)
    print(f"✅ Saved → {args.output} | rows={len(df)}")
    print(f"  prop_norm values: {df['prop_norm'].value_counts().to_dict()}")
    print(f"  pick_type values: {df['pick_type'].value_counts().to_dict()}")
    blanks = int((df['opp_team_abbr'].astype(str).str.strip() == "").sum())
    print(f"  opp_team_abbr blank: {blanks}/{len(df)}")
    if len(df) and blanks:
        log_pipeline_health(
            "cbb.step2_normalize",
            "opp_team_blank",
            extra={"blank": blanks, "total": len(df), "output": args.output},
            start=Path(__file__),
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_pipeline_health(
            "cbb.step2_normalize",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        raise
