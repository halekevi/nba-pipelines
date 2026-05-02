#!/usr/bin/env python3
"""
nhl_defense_report.py

Pull ALL NHL team defense + pace context (current season) from the NHL Stats API
and output:
  - nhl_defense_summary.csv (all 32 teams)
  - printed leaderboards (overall defense, GAA, shots against, PK%, pace)

Mirrors the structure of NBA defense_report.py exactly:
  - Composite OVERALL_DEF_RANK (weighted rank-of-ranks, Option B style)
  - DEF_TIER: Elite / Above Avg / Avg / Below Avg / Weak
  - Separate skater-context and goalie-context metrics

Usage:
  py -3 nhl_defense_report.py
  py -3 nhl_defense_report.py --season 20242025 --out cache/nhl_defense_summary.csv --top 10

Output columns (compatible with NHL_step3_attach_defense_nhl.py):
  team              - uppercase 3-letter abbreviation (EDM, TOR, BOS...)
  opp_gaa           - goals against per game
  opp_saa           - shots against per game
  opp_pk_pct        - penalty kill percentage
  opp_gf_per_game   - goals for per game  (goalie context: opp offensive threat)
  opp_sf_per_game   - shots for per game
  opp_pp_pct        - power play percentage
  opp_wins          - wins
  opp_gp            - games played
  OVERALL_DEF_RANK  - 1 = best defense (composite)
  DEF_TIER          - Elite / Above Avg / Avg / Below Avg / Weak
  def_rank          - rank by GAA alone (matches step3 field name)
  def_tier          - same as DEF_TIER (matches step3 field name)

No API key required — uses public NHL Stats API.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.defense_tiers import def_tier_from_overall_rank

NHL_API   = "https://api.nhle.com/stats/rest/en"
NHL_WEB   = "https://api-web.nhle.com/v1"
HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

_today_season = date.today()
_start_year = _today_season.year if _today_season.month >= 10 else _today_season.year - 1
DEFAULT_SEASON = f"{_start_year}{_start_year + 1}"

# Known NHL team abbreviation fixes (rare edge cases)
TEAM_ALIAS_FIX = {
    "T.B": "TBL", "S.J": "SJS", "N.J": "NJD",
    "L.A": "LAK", "VGK": "VGK",
}


def norm_team_abbr(x: Any) -> str:
    if x is None:
        return "UNK"
    s = str(x).strip().upper()
    if s in ("", "NAN", "NONE", "NULL"):
        return "UNK"
    return TEAM_ALIAS_FIX.get(s, s)


def fetch_json(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.5 ** attempt)
    return {}


def rank_series(s: pd.Series, ascending: bool) -> pd.Series:
    return s.rank(method="min", ascending=ascending).astype("Int64")


def pull_team_stats(season: str) -> pd.DataFrame:
    """
    Pull per-game team stats from NHL Stats REST API.
    Returns one row per team with both offensive and defensive metrics.
    """
    url = (
        f"{NHL_API}/team"
        f"?isAggregate=false&isGame=false"
        f"&sort=%5B%7B%22property%22%3A%22gamesPlayed%22%2C%22direction%22%3A%22DESC%22%7D%5D"
        f"&start=0&limit=50"
        f"&factCayenneExp=gamesPlayed%3E%3D1"
        f"&cayenneExp=gameTypeId%3D2%20and%20seasonId%3E%3D{season}%20and%20seasonId%3C%3D{season}"
    )
    print(f"📡 Fetching NHL team stats (season {season})...")
    data = fetch_json(url)
    records = data.get("data", [])

    if not records:
        print("  ⚠️  Primary endpoint returned no data — trying standings fallback...")
        return pull_from_standings(season)

    rows = []
    for rec in records:
        abbrev = norm_team_abbr(rec.get("teamAbbrev", ""))
        if not abbrev or abbrev == "UNK":
            continue

        gp  = int(rec.get("gamesPlayed", 0) or 0)
        gaa = float(rec.get("goalsAgainstPerGame", 0.0) or 0.0)
        saa = float(rec.get("shotsAgainstPerGame", 0.0) or 0.0)
        pk  = float(rec.get("penaltyKillPct", 0.0) or 0.0)
        gf  = float(rec.get("goalsForPerGame", 0.0) or 0.0)
        sf  = float(rec.get("shotsForPerGame", 0.0) or 0.0)
        pp  = float(rec.get("powerPlayPct", 0.0) or 0.0)
        w   = int(rec.get("wins", 0) or 0)

        # Derived: shots-to-goals ratio allowed (lower = stingier defense)
        sa_ratio = round(saa / max(gaa, 0.01), 2) if gaa > 0 else 0.0

        rows.append({
            "team":            abbrev,
            "opp_gp":          gp,
            "opp_gaa":         round(gaa, 3),
            "opp_saa":         round(saa, 3),
            "opp_pk_pct":      round(pk, 3),
            "opp_gf_per_game": round(gf, 3),
            "opp_sf_per_game": round(sf, 3),
            "opp_pp_pct":      round(pp, 3),
            "opp_wins":        w,
            "sa_ratio":        sa_ratio,
        })

    print(f"  ✅ Got stats for {len(rows)} teams")
    return pd.DataFrame(rows)


def pull_from_standings(season: str) -> pd.DataFrame:
    """Fallback: derive defensive metrics from standings endpoint."""
    print("  Fetching from standings endpoint...")
    data = fetch_json(f"{NHL_WEB}/standings/now")
    standings = data.get("standings", [])

    rows = []
    for entry in standings:
        abbrev = norm_team_abbr(
            entry.get("teamAbbrev", {}).get("default", "")
        )
        if not abbrev or abbrev == "UNK":
            continue

        gp = int(entry.get("gamesPlayed", 1) or 1)
        ga = int(entry.get("goalAgainst", 0) or 0)
        gf = int(entry.get("goalFor", 0) or 0)
        w  = int(entry.get("wins", 0) or 0)

        rows.append({
            "team":            abbrev,
            "opp_gp":          gp,
            "opp_gaa":         round(ga / max(gp, 1), 3),
            "opp_saa":         0.0,
            "opp_pk_pct":      0.0,
            "opp_gf_per_game": round(gf / max(gp, 1), 3),
            "opp_sf_per_game": 0.0,
            "opp_pp_pct":      0.0,
            "opp_wins":        w,
            "sa_ratio":        0.0,
        })

    print(f"  ✅ Standings fallback: {len(rows)} teams")
    return pd.DataFrame(rows)


def add_ranks_and_tiers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n  = len(df)

    # ── Individual metric ranks (lower allowed = better defense = rank 1) ──
    if "opp_gaa" in df.columns:
        df["opp_gaa_rank"]    = rank_series(df["opp_gaa"], ascending=True)   # lower GAA = better
    if "opp_saa" in df.columns:
        df["opp_saa_rank"]    = rank_series(df["opp_saa"], ascending=True)   # lower shots against = better
    if "opp_pk_pct" in df.columns:
        df["opp_pk_pct_rank"] = rank_series(df["opp_pk_pct"], ascending=False) # higher PK% = better
    if "sa_ratio" in df.columns:
        df["sa_ratio_rank"]   = rank_series(df["sa_ratio"], ascending=False) # higher SA ratio = stingier (more shots needed per goal)

    # Offensive threat rank (for goalie matchup context): higher opp_gf = tougher for goalies
    if "opp_gf_per_game" in df.columns:
        df["opp_gf_rank"]     = rank_series(df["opp_gf_per_game"], ascending=False)  # 1 = most dangerous offence

    # ── Pace proxy: shots for per game (higher SF = faster/higher tempo team) ──
    if "opp_sf_per_game" in df.columns:
        df["pace_fast_rank"]  = rank_series(df["opp_sf_per_game"], ascending=False)  # 1 = most shots (fastest)
        df["pace_slow_rank"]  = rank_series(df["opp_sf_per_game"], ascending=True)   # 1 = fewest shots (slowest)

    # ── Composite OVERALL_DEF_RANK (Option B: weighted rank-of-ranks) ──
    # Mirror NBA defense_report.py composite logic
    rank_cols = [c for c in ["opp_gaa_rank", "opp_saa_rank", "opp_pk_pct_rank", "sa_ratio_rank"]
                 if c in df.columns]

    if rank_cols:
        weights = {
            "opp_gaa_rank":    3.0,   # primary — goals allowed per game
            "opp_saa_rank":    2.0,   # shots against volume
            "opp_pk_pct_rank": 1.5,   # penalty kill discipline
            "sa_ratio_rank":   1.0,   # shots-to-goals ratio
        }
        w = np.array([weights.get(c, 1.0) for c in rank_cols], dtype=float)
        w = w / w.sum()

        df["OVERALL_DEF_SCORE"] = df[rank_cols].apply(pd.to_numeric, errors="coerce").mul(w, axis=1).sum(axis=1)
        df["OVERALL_DEF_RANK"]  = rank_series(df["OVERALL_DEF_SCORE"], ascending=True)
    else:
        df["OVERALL_DEF_SCORE"] = np.nan
        df["OVERALL_DEF_RANK"]  = pd.NA

    # ── DEF_TIER: quintiles over n teams (rank 1 = best defense) ──
    def tier_from_rank(r) -> str:
        return def_tier_from_overall_rank(r, n)

    df["DEF_TIER"] = df["OVERALL_DEF_RANK"].apply(tier_from_rank)

    # ── Legacy field aliases for step3 compatibility ──
    df["def_rank"] = df["opp_gaa_rank"]   # step3 uses def_rank (GAA-based rank)
    df["def_tier"] = df["DEF_TIER"].str.upper().map({
        "ELITE":      "ELITE",
        "ABOVE AVG":  "SOLID",
        "AVG":        "AVERAGE",
        "BELOW AVG":  "WEAK",
        "WEAK":       "WEAK",
    }).fillna("AVERAGE")                  # step3 coarse buckets; Below Avg → softer matchup

    return df


def print_leaders(df: pd.DataFrame, topn: int) -> None:
    def show(title: str, col: str, display_col: str = None, asc: bool = True):
        if col not in df.columns:
            print(f"\n{title}: (missing {col})")
            return
        dc = display_col or col
        cols_to_show = ["team", dc] if dc != col else ["team", col]
        cols_to_show = [c for c in cols_to_show if c in df.columns]
        t = df.sort_values(col, ascending=asc).head(topn)[cols_to_show]
        print(f"\n{title}")
        print(t.to_string(index=False))

    print("\n" + "=" * 60)
    print("NHL TEAM DEFENSE LEADERBOARDS")
    print("=" * 60)
    show("BEST OVERALL DEFENSE (composite) — OVERALL_DEF_RANK",
         "OVERALL_DEF_RANK", asc=True)
    show("LOWEST GOALS AGAINST/GAME — opp_gaa_rank",
         "opp_gaa", asc=True)
    show("FEWEST SHOTS AGAINST/GAME — opp_saa_rank",
         "opp_saa", asc=True)
    show("BEST PENALTY KILL % — opp_pk_pct_rank",
         "opp_pk_pct", asc=False)
    show("MOST DANGEROUS OFFENCE (goalie matchup) — opp_gf_rank",
         "opp_gf_per_game", asc=False)
    show("SLOWEST PACE (shots for) — pace_slow_rank",
         "opp_sf_per_game", asc=True)
    show("FASTEST PACE (shots for) — pace_fast_rank",
         "opp_sf_per_game", asc=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season",  default=DEFAULT_SEASON,
                    help="Season ID, e.g. 20252026 (default: current season from date)")
    ap.add_argument("--out",     default="cache/nhl_defense_summary.csv")
    ap.add_argument("--top",     type=int, default=10)
    args = ap.parse_args()
    print(f"NHL defense report: season {args.season}")

    df = pull_team_stats(season=args.season)

    if df.empty:
        print("❌ No data returned. Check your internet connection.")
        return

    df = add_ranks_and_tiers(df)

    # Clean column order
    front = [
        "team", "opp_gp",
        "opp_gaa", "opp_saa", "opp_pk_pct",
        "opp_gf_per_game", "opp_sf_per_game", "opp_pp_pct",
        "opp_wins", "sa_ratio",
        "opp_gaa_rank", "opp_saa_rank", "opp_pk_pct_rank",
        "opp_gf_rank", "pace_fast_rank", "pace_slow_rank",
        "OVERALL_DEF_SCORE", "OVERALL_DEF_RANK", "DEF_TIER",
        "def_rank", "def_tier",
    ]
    df = df[[c for c in front if c in df.columns]]

    out_path = args.out
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    except PermissionError:
        stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_path.replace(".csv", f"_{stamp}.csv")
        df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print_leaders(df, topn=args.top)
    print(f"\n✅ Saved → {out_path}")
    print(f"Teams: {df['team'].nunique()}")
    print("\nDEF_TIER breakdown:")
    print(df["DEF_TIER"].value_counts().to_string())

    # ── Write to proporacle_ref.db ───────────────────────────────────────────────
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "scripts"))
        from defense_db import write_defense_to_db
        write_defense_to_db(df, sport="nhl")
    except Exception as _e:
        print(f"  ⚠️  Could not write to DB: {_e}")


if __name__ == "__main__":
    main()
