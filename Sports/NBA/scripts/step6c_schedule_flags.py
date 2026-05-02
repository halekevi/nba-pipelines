#!/usr/bin/env python3
"""
step6c_schedule_flags.py
─────────────────────────
PropOracle-NBA-S6c: Schedule Fatigue Flags (Back-to-Back & Rest Days)

PURPOSE:
  Computes days_rest and b2b_flag for every player/prop row based on the
  NBA schedule. Players on back-to-back games average 15-20% lower stats.
  This step adds that context so step7 can penalize those picks.

  Also fixes the pp_home_team / pp_away_team 100% null problem by deriving
  home/away from start_time + schedule data.

INPUTS:
  --input   step6b_with_game_context.csv  (or step6_with_team_role_context.csv)
  --output  step6c_with_schedule_flags.csv
  --date    Slate date YYYY-MM-DD (default: today)
  --cache   Optional schedule cache CSV

OUTPUTS:
  step6c_with_schedule_flags.csv — input + these new columns:
    days_rest       int     Days since team's last game (0 = B2B, 1 = 1-day rest...)
    b2b_flag        bool    True if days_rest == 0 (back-to-back)
    home_away       str     "HOME" | "AWAY" | "UNKNOWN"
    rest_adj        float   Edge adjustment for fatigue (-0.08 for B2B, 0.05 for 3+)
    schedule_source str     "api" | "cache" | "fallback"

B2B IMPACT REFERENCE (NBA research):
  B2B (0 days rest):  avg -15% to -20% stat output
  1 day rest:          baseline
  2 days rest:         +2-3%
  3+ days rest:        +4-5%

USAGE:
  py -3.14 step6c_schedule_flags.py \
    --input step6b_with_game_context.csv \
    --output step6c_with_schedule_flags.csv \
    --date 2026-03-06

AUTHOR: PropOracle Pipeline
VERSION: 1.0 (March 2026)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import urllib.request as _urllib
    import urllib.parse as _urlparse
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False


# ── CONFIG ────────────────────────────────────────────────────────────────────

# Edge adjustments by rest days
REST_ADJ = {
    0: -0.10,   # B2B — significant fatigue penalty
    1: 0.00,    # 1 day rest — baseline
    2: 0.02,    # 2 days rest — slight boost
    3: 0.04,    # 3+ days rest — fresh legs boost
}
REST_ADJ_3PLUS = 0.04  # applied when days_rest >= 3

# ESPN schedule endpoint (no auth needed)
ESPN_SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# Team name → abbreviation map for ESPN schedule parsing
ESPN_TEAM_MAP: Dict[str, str] = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BRK",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

# ESPN abbreviation → pipeline abbreviation
ESPN_ABBR_MAP: Dict[str, str] = {
    "BKN": "BRK", "GS":  "GSW", "NO":  "NOP", "NY":  "NYK",
    "PHO": "PHX", "SA":  "SAS", "CLE": "CLE", "OKC": "OKC",
}


def clean_abbr(abbr: str) -> str:
    """Strip combo prop slashes: 'PHX/NOP' → 'PHX'"""
    if not abbr or pd.isna(abbr):
        return ""
    return str(abbr).split("/")[0].strip().upper()


def norm_espn_abbr(abbr: str) -> str:
    a = str(abbr).upper().strip()
    return ESPN_ABBR_MAP.get(a, a)


# ── ESPN SCHEDULE FETCH ───────────────────────────────────────────────────────

def fetch_espn_schedule(date_str: str) -> Optional[dict]:
    """Fetch ESPN scoreboard for a given date."""
    compact = date_str.replace("-", "")
    url = f"{ESPN_SCHEDULE_URL}?dates={compact}&limit=20"
    print(f"  [6c] Fetching ESPN schedule: {url}")
    try:
        req = _urllib.Request(url, headers={"User-Agent": "PropOracle/1.0"})
        with _urllib.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [6c] WARNING: ESPN schedule fetch failed: {e}")
        return None


def fetch_espn_schedule_range(date_str: str, days_back: int = 7) -> Dict[str, List[str]]:
    """
    Fetch schedules for date_str and days_back previous days.
    Returns {team_abbr: [sorted list of game dates played]} 
    so we can compute days_rest for today.
    """
    game_dates: Dict[str, List[str]] = {}  # team → list of "YYYY-MM-DD" played

    target = datetime.strptime(date_str, "%Y-%m-%d")

    for delta in range(1, days_back + 1):
        check_date = (target - timedelta(days=delta)).strftime("%Y-%m-%d")
        data = fetch_espn_schedule(check_date)
        if not data:
            continue

        events = data.get("events", [])
        for event in events:
            status = event.get("status", {}).get("type", {}).get("completed", False)
            # Include any game (completed or scheduled) to map team game dates
            comps = event.get("competitions", [{}])[0]
            game_date_raw = event.get("date", "")[:10]  # "YYYY-MM-DD"

            for competitor in comps.get("competitors", []):
                abbr = norm_espn_abbr(
                    competitor.get("team", {}).get("abbreviation", "")
                )
                if abbr:
                    if abbr not in game_dates:
                        game_dates[abbr] = []
                    if game_date_raw and game_date_raw not in game_dates[abbr]:
                        game_dates[abbr].append(game_date_raw)

    # Sort dates ascending
    for abbr in game_dates:
        game_dates[abbr] = sorted(game_dates[abbr])

    return game_dates


def fetch_home_away_for_date(date_str: str) -> Dict[str, str]:
    """
    Fetch today's schedule and return {team_abbr: "HOME"|"AWAY"}.
    """
    data = fetch_espn_schedule(date_str)
    result: Dict[str, str] = {}
    if not data:
        return result

    for event in data.get("events", []):
        comps = event.get("competitions", [{}])[0]
        for competitor in comps.get("competitors", []):
            abbr     = norm_espn_abbr(competitor.get("team", {}).get("abbreviation", ""))
            home_away = competitor.get("homeAway", "").upper()  # "home" or "away"
            if abbr:
                result[abbr] = "HOME" if home_away == "HOME" else "AWAY"

    return result


# ── REST CALCULATION ──────────────────────────────────────────────────────────

def compute_days_rest(team_abbr: str, slate_date: str,
                      game_history: Dict[str, List[str]]) -> int:
    """
    Compute days_rest for a team as of slate_date.
    Returns:
      0 = played yesterday (B2B)
      1 = played 2 days ago
      N = days since last game
      99 = no recent game found (treat as well-rested)
    """
    target = datetime.strptime(slate_date, "%Y-%m-%d")
    past_games = game_history.get(team_abbr, [])

    if not past_games:
        return 99  # no data — assume rested

    # Find most recent game BEFORE today
    for gdate_str in reversed(past_games):
        gdate = datetime.strptime(gdate_str, "%Y-%m-%d")
        if gdate < target:
            delta = (target - gdate).days - 1  # 0 = B2B
            return max(0, delta)

    return 99


def rest_to_adj(days: int) -> float:
    if days == 0:
        return REST_ADJ[0]
    elif days == 1:
        return REST_ADJ[1]
    elif days == 2:
        return REST_ADJ[2]
    elif days == 99:
        return 0.0   # unknown — neutral, don't boost or penalize
    else:
        return REST_ADJ_3PLUS


# ── CACHE ─────────────────────────────────────────────────────────────────────

def load_cache(path: str) -> Optional[dict]:
    p = Path(path)
    if p.exists():
        try:
            df = pd.read_csv(p)
            result = {}
            for _, row in df.iterrows():
                result[row["team"]] = {
                    "days_rest": int(row.get("days_rest", 99)),
                    "b2b_flag":  bool(row.get("b2b_flag", False)),
                    "home_away": str(row.get("home_away", "UNKNOWN")),
                }
            print(f"  [6c] Loaded schedule cache: {path} ({len(result)} teams)")
            return result
        except Exception as e:
            print(f"  [6c] Cache load failed: {e}")
    return None


def save_cache(team_data: dict, path: str):
    rows = []
    for team, d in team_data.items():
        rows.append({"team": team, **d})
    try:
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"  [6c] Saved schedule cache → {path}")
    except Exception as e:
        print(f"  [6c] Cache save failed: {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Step 6c: Schedule flags (B2B, rest days, home/away)")
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--date",   default="")
    ap.add_argument("--cache",  default="")
    ap.add_argument("--days-back", type=int, default=7,
                    help="Days of schedule history to fetch (default: 7)")
    args = ap.parse_args()

    date_str   = args.date or datetime.now().strftime("%Y-%m-%d")
    cache_path = args.cache or f"schedule_cache_{date_str}.csv"

    print(f"\n{'='*60}")
    print(f"  STEP 6C — Schedule Flags (B2B / Rest / Home-Away)")
    print(f"  Date: {date_str}")
    print(f"{'='*60}\n")

    # Load slate
    print(f"  [6c] Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    print(f"  [6c] Loaded {len(df)} rows")

    # Get unique teams in today's slate
    teams_today = set(df["team"].dropna().apply(clean_abbr).unique())
    teams_today = {t for t in teams_today if t and "/" not in t}
    print(f"  [6c] Teams in slate: {sorted(teams_today)}")

    # ── Resolve schedule data ─────────────────────────────────────────────────
    cached = load_cache(cache_path)

    if cached:
        team_schedule_data = cached
        source = "cache"
    else:
        print(f"  [6c] Fetching schedule history ({args.days_back} days back)…")
        game_history = fetch_espn_schedule_range(date_str, args.days_back)
        home_away_map = fetch_home_away_for_date(date_str)

        team_schedule_data = {}
        for team in teams_today:
            days = compute_days_rest(team, date_str, game_history)
            b2b  = days == 0
            ha   = home_away_map.get(team, "UNKNOWN")
            team_schedule_data[team] = {
                "days_rest": days,
                "b2b_flag":  b2b,
                "home_away": ha,
            }

        save_cache(team_schedule_data, cache_path)
        source = "api"

    # ── Apply to slate ────────────────────────────────────────────────────────
    # Drop existing columns if re-running
    for col in ["days_rest", "b2b_flag", "home_away", "rest_adj", "schedule_source"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    def get_days_rest(team_raw):
        t = clean_abbr(str(team_raw))
        return team_schedule_data.get(t, {}).get("days_rest", 99)

    def get_b2b(team_raw):
        t = clean_abbr(str(team_raw))
        return team_schedule_data.get(t, {}).get("b2b_flag", False)

    def get_home_away(team_raw):
        t = clean_abbr(str(team_raw))
        return team_schedule_data.get(t, {}).get("home_away", "UNKNOWN")

    df["days_rest"]        = df["team"].apply(get_days_rest)
    df["b2b_flag"]         = df["team"].apply(get_b2b).astype(bool)
    df["home_away"]        = df["team"].apply(get_home_away)
    df["rest_adj"]         = df["days_rest"].apply(rest_to_adj)
    df["schedule_source"]  = source

    # Fix pp_home_team / pp_away_team nulls using home_away data
    if "pp_home_team" in df.columns:
        df["pp_home_team"] = df["pp_home_team"].astype(object)
        df["pp_away_team"] = df["pp_away_team"].astype(object)
        mask_null = df["pp_home_team"].isna() | (df["pp_home_team"] == "")
        home_mask = mask_null & (df["home_away"] == "HOME")
        away_mask = mask_null & (df["home_away"] == "AWAY")
        if home_mask.any():
            df.loc[home_mask, "pp_home_team"] = df.loc[home_mask, "team"].apply(clean_abbr)
        if away_mask.any():
            df.loc[away_mask, "pp_away_team"] = df.loc[away_mask, "team"].apply(clean_abbr)

    # ── Summary ───────────────────────────────────────────────────────────────
    b2b_count  = df["b2b_flag"].sum()
    rested     = (df["days_rest"] >= 3).sum()

    print(f"\n  [6c] Schedule Summary:")
    print(f"  {'Team':>5}  {'Days Rest':>10}  {'B2B':>5}  {'Home/Away':>10}  {'Adj':>6}")
    print(f"  {'-'*45}")
    for team, d in sorted(team_schedule_data.items()):
        dr   = d["days_rest"]
        b2b  = "✅ B2B" if d["b2b_flag"] else ""
        ha   = d["home_away"]
        adj  = rest_to_adj(dr)
        dr_s = "B2B(0)" if dr == 0 else ("7+" if dr >= 7 else str(dr))
        print(f"  {team:>5}  {dr_s:>10}  {b2b:>5}  {ha:>10}  {adj:>+6.2f}")

    print(f"\n  [6c] B2B props flagged: {b2b_count}")
    print(f"  [6c] Well-rested props (3+ days): {rested}")

    # ── Save ──────────────────────────────────────────────────────────────────
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"\n  [6c] ✅ Saved {len(df)} rows → {args.output}")
    print(f"  [6c] New columns: days_rest, b2b_flag, home_away, rest_adj, schedule_source\n")


if __name__ == "__main__":
    main()
