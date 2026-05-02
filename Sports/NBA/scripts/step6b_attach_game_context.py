#!/usr/bin/env python3
"""
step6b_attach_game_context.py
─────────────────────────────
PropOracle-NBA-S6b: Vegas Game Context (Totals, Spreads, Blowout Risk)

PURPOSE:
  Fetches live Vegas odds (game total O/U, spread, implied team totals)
  from The Odds API (free tier: 500 req/month) and attaches them to every
  prop row. Adds a blowout_risk flag and a low_total_flag that downstream
  steps use to downweight risky props.

  Catches the "Jalen Green problem": low game total + fast pace vs slow pace
  team = fewer possessions = combo props miss. This step surfaces that risk
  BEFORE picks are locked.

INPUTS:
  --input   step6_with_team_role_context.csv
  --output  step6b_with_game_context.csv
  --api_key Your Odds API key (free at the-odds-api.com)
            OR set env var ODDS_API_KEY
  --cache   Optional: game_context_cache_YYYY-MM-DD.csv (skip API if exists)
  --date    Date of slate (YYYY-MM-DD), defaults to today

OUTPUTS:
  step6b_with_game_context.csv — input + these new columns:
    game_total          float   Combined O/U line (e.g. 224.5)
    spread              float   Team's point spread (negative = favorite)
    implied_team_total  float   Team's expected points = (total/2) - (spread/2)
    blowout_risk        bool    abs(spread) > BLOWOUT_THRESHOLD (default 8.5)
    low_total_flag      bool    game_total < LOW_TOTAL_THRESHOLD (default 215)
    odds_source         str     "live" | "cache" | "fallback"

  game_context_cache_YYYY-MM-DD.csv — cached odds for re-use / grading

EDGE ADJUSTMENTS (applied here, used in step7+):
  - low_total_flag=True  → combo props get ctx_adj = -0.08
  - blowout_risk=True    → minutes_certainty penalized (applied in step7)
  - Both flags           → ctx_adj = -0.15

USAGE:
  py -3.14 step6b_attach_game_context.py \
    --input step6_with_team_role_context.csv \
    --output step6b_with_game_context.csv \
    --api_key YOUR_KEY_HERE \
    --date 2026-03-06

  # Or via env var:
  set ODDS_API_KEY=YOUR_KEY_HERE
  py -3.14 step6b_attach_game_context.py --input ... --output ...

GET YOUR FREE KEY: https://the-odds-api.com  (500 free requests/month)

AUTHOR: PropOracle Pipeline
VERSION: 1.0 (March 2026)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import urllib.request as _urllib
    import urllib.parse as _urlparse
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

# ── CONFIG ────────────────────────────────────────────────────────────────────

ODDS_API_BASE    = "https://api.the-odds-api.com/v4"
SPORT_KEY        = "basketball_nba"
REGIONS          = "us"
MARKETS          = "totals,spreads"
ODDS_FORMAT      = "american"
BLOWOUT_THRESHOLD = 8.5    # abs(spread) > this = blowout risk
LOW_TOTAL_THRESH  = 215.0  # game_total < this = low scoring flag
HIGH_TOTAL_THRESH = 228.0  # game_total > this = high pace / fast game
COMBO_CTX_ADJ     = -0.08  # edge adj for low total on combo props
BLOWOUT_CTX_ADJ   = -0.05  # edge adj for blowout risk
BOTH_ADJ          = -0.15  # when both flags triggered

# Pace tiers based on game total O/U
# Fast (228+): high possessions, props inflate → OVER friendly
# Normal (215-228): baseline
# Slow (<215): low possessions → combo props deflate, UNDERs more reliable
def _pace_tier(game_total):
    if pd.isna(game_total):
        return "UNKNOWN"
    if game_total >= HIGH_TOTAL_THRESH:
        return "FAST"
    if game_total >= LOW_TOTAL_THRESH:
        return "NORMAL"
    return "SLOW"

# Maps pipeline 3-letter team abbr → Odds API team name fragments
# The Odds API uses full team names; we fuzzy-match on these keywords
TEAM_NAME_MAP: Dict[str, str] = {
    "ATL": "Atlanta",   "BOS": "Boston",    "BRK": "Brooklyn",  "BKN": "Brooklyn",
    "CHA": "Charlotte", "CHI": "Chicago",   "CLE": "Cleveland", "DAL": "Dallas",
    "DEN": "Denver",    "DET": "Detroit",   "GSW": "Golden State", "GS": "Golden State",
    "HOU": "Houston",   "IND": "Indiana",   "LAC": "Clippers",  "LAL": "Lakers",
    "MEM": "Memphis",   "MIA": "Miami",     "MIL": "Milwaukee", "MIN": "Minnesota",
    "NOP": "New Orleans", "NYK": "Knicks",  "OKC": "Oklahoma",  "ORL": "Orlando",
    "PHI": "Philadelphia", "PHX": "Phoenix","POR": "Portland",  "SAC": "Sacramento",
    "SAS": "San Antonio","TOR": "Toronto",  "UTA": "Utah",      "WAS": "Washington",
}


# ── TEAM ABBR HELPERS ─────────────────────────────────────────────────────────

def clean_abbr(abbr: str) -> str:
    """Strip combo-prop slashes: 'PHX/NOP' → 'PHX'"""
    if not abbr or pd.isna(abbr):
        return ""
    return str(abbr).split("/")[0].strip().upper()


def abbr_to_keyword(abbr: str) -> str:
    a = clean_abbr(abbr)
    return TEAM_NAME_MAP.get(a, a)


# ── ODDS API FETCH ────────────────────────────────────────────────────────────

def fetch_odds(api_key: str, date_str: str) -> Optional[list]:
    """
    Fetch NBA odds from The Odds API.
    Returns list of game dicts or None on failure.
    """
    params = {
        "apiKey":     api_key,
        "regions":    REGIONS,
        "markets":    MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    qs = _urlparse.urlencode(params)
    url = f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds?{qs}"

    print(f"  [6b] Fetching odds from The Odds API…")
    try:
        req = _urllib.Request(url, headers={"User-Agent": "PropOracle/1.0"})
        with _urllib.urlopen(req, timeout=20) as resp:
            remaining = resp.headers.get("x-requests-remaining", "?")
            used = resp.headers.get("x-requests-used", "?")
            print(f"  [6b] API quota — used: {used}, remaining: {remaining}")
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  [6b] Got {len(data)} games from API")
            return data
    except Exception as e:
        print(f"  [6b] WARNING: Odds API fetch failed: {e}")
        return None


# ── PARSE ODDS INTO LOOKUP ────────────────────────────────────────────────────

def parse_odds_to_lookup(games: list) -> Dict[Tuple[str, str], Dict]:
    """
    Convert raw API response → dict keyed by (home_kw, away_kw) → odds dict.
    Also builds reverse (away_kw, home_kw) for flexibility.

    Returns {(team_fragment, opp_fragment): {total, spread_home, spread_away, ...}}
    """
    lookup: Dict[Tuple[str, str], Dict] = {}

    for game in games:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        bookmakers = game.get("bookmakers", [])

        game_total    = None
        spread_home   = None
        spread_away   = None

        # Prefer DraftKings → FanDuel → first available
        priority = ["draftkings", "fanduel", "betmgm", "caesars"]
        def bm_score(bm):
            k = bm.get("key", "").lower()
            for i, p in enumerate(priority):
                if p in k:
                    return i
            return 99
        bookmakers_sorted = sorted(bookmakers, key=bm_score)

        for bm in bookmakers_sorted:
            for market in bm.get("markets", []):
                mkey = market.get("key", "")
                outcomes = market.get("outcomes", [])

                if mkey == "totals" and game_total is None:
                    for o in outcomes:
                        if o.get("name") == "Over":
                            game_total = float(o.get("point", 0) or 0)
                            break

                elif mkey == "spreads":
                    for o in outcomes:
                        name = o.get("name", "")
                        pt   = float(o.get("point", 0) or 0)
                        if home.lower() in name.lower() and spread_home is None:
                            spread_home = pt
                        elif away.lower() in name.lower() and spread_away is None:
                            spread_away = pt
            if game_total and spread_home is not None:
                break  # found what we need

        record = {
            "game_total":    game_total,
            "spread_home":   spread_home,
            "spread_away":   spread_away,
            "pace_tier":     _pace_tier(game_total),
            "home_team_full": home,
            "away_team_full": away,
            "commence_time":  game.get("commence_time", ""),
        }

        # Build keyword tuples for matching
        home_kw = home.lower()
        away_kw = away.lower()
        lookup[(home_kw, away_kw)] = record
        lookup[(away_kw, home_kw)] = {"game_total": game_total,
                                       "spread_home": spread_away,   # flipped
                                       "spread_away": spread_home,
                                       "home_team_full": away,
                                       "away_team_full": home,
                                       "commence_time": game.get("commence_time","")}

    return lookup


def find_game(team_abbr: str, opp_abbr: str, lookup: Dict) -> Optional[Dict]:
    """Fuzzy-match team/opp abbreviations to lookup keys."""
    team_kw = abbr_to_keyword(team_abbr).lower()
    opp_kw  = abbr_to_keyword(opp_abbr).lower()

    # Direct match
    for (h, a), rec in lookup.items():
        if team_kw in h and opp_kw in a:
            return {"game_total": rec["game_total"], "spread": rec["spread_home"]}
        if team_kw in a and opp_kw in h:
            return {"game_total": rec["game_total"], "spread": rec["spread_away"]}

    return None


# ── BUILD GAME-LEVEL ODDS TABLE ───────────────────────────────────────────────

def build_game_odds_table(df: pd.DataFrame, lookup: Dict) -> pd.DataFrame:
    """
    For each unique (team, opp_team) pair, resolve odds and return a
    DataFrame that can be merged back onto the full slate.
    """
    pairs = df[["team", "opp_team"]].drop_duplicates()
    rows = []

    for _, row in pairs.iterrows():
        team = clean_abbr(row["team"])
        opp  = clean_abbr(row["opp_team"])
        rec  = find_game(team, opp, lookup)

        if rec and rec["game_total"]:
            gt     = float(rec["game_total"])
            spread = float(rec["spread"]) if rec["spread"] is not None else 0.0
            itotal = round((gt / 2) - (spread / 2), 1)
            blowout = abs(spread) > BLOWOUT_THRESHOLD
            low_tot = gt < LOW_TOTAL_THRESH
            pace    = _pace_tier(gt)
            source  = "live"
        else:
            gt, spread, itotal = None, None, None
            blowout = False
            low_tot = False
            pace    = "UNKNOWN"
            source  = "fallback"
            print(f"  [6b] WARNING: No odds found for {team} vs {opp}")

        rows.append({
            "team":               row["team"],
            "opp_team":           row["opp_team"],
            "game_total":         gt,
            "spread":             spread,
            "implied_team_total": itotal,
            "blowout_risk":       blowout,
            "low_total_flag":     low_tot,
            "pace_tier":          pace,
            "odds_source":        source,
        })

    return pd.DataFrame(rows)


# ── CONTEXT ADJUSTMENT ────────────────────────────────────────────────────────

# Props that are "combo" and heavily affected by pace/total
COMBO_PROPS = {
    "pra", "pr", "pa", "ra", "fantasy", "fantasy score",
    "pts+rebs+asts", "pts+rebs", "pts+asts", "rebs+asts",
    "blks+stls",
}

def compute_ctx_adj(row: pd.Series) -> float:
    """
    Compute a context adjustment penalty based on game context flags.
    Applied to edge/projection in step7.
    """
    prop_norm = str(row.get("prop_norm", "")).lower()
    is_combo  = row.get("is_combo_player", 0) or (prop_norm in COMBO_PROPS)
    low_tot   = bool(row.get("low_total_flag", False))
    blowout   = bool(row.get("blowout_risk", False))

    if low_tot and blowout and is_combo:
        return BOTH_ADJ
    elif low_tot and is_combo:
        return COMBO_CTX_ADJ
    elif blowout:
        return BLOWOUT_CTX_ADJ
    return 0.0


# ── CACHE ─────────────────────────────────────────────────────────────────────

def load_cache(cache_path: str) -> Optional[pd.DataFrame]:
    p = Path(cache_path)
    if p.exists():
        try:
            df = pd.read_csv(p)
            print(f"  [6b] Loaded odds cache: {cache_path} ({len(df)} rows)")
            return df
        except Exception as e:
            print(f"  [6b] Cache load failed: {e}")
    return None


def save_cache(game_odds: pd.DataFrame, cache_path: str):
    try:
        game_odds.to_csv(cache_path, index=False)
        print(f"  [6b] Saved odds cache → {cache_path}")
    except Exception as e:
        print(f"  [6b] Cache save failed: {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Step 6b: Attach Vegas game context to NBA slate")
    ap.add_argument("--input",   required=True, help="step6_with_team_role_context.csv")
    ap.add_argument("--output",  required=True, help="step6b_with_game_context.csv")
    ap.add_argument("--api_key", default="",    help="The Odds API key (or set ODDS_API_KEY env var)")
    ap.add_argument("--cache",   default="",    help="Path to cache CSV (avoids API hit if exists)")
    ap.add_argument("--date",    default="",    help="Slate date YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    # Resolve API key
    api_key = args.api_key or os.environ.get("ODDS_API_KEY", "")

    # Resolve date
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    # Auto-set cache path if not provided
    cache_path = args.cache or f"game_context_cache_{date_str}.csv"

    print(f"\n{'='*60}")
    print(f"  STEP 6B — Game Context (Vegas Lines)")
    print(f"  Date: {date_str}")
    print(f"{'='*60}\n")

    # Load input slate
    print(f"  [6b] Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    print(f"  [6b] Loaded {len(df)} rows, {len(df.columns)} columns")

    # ── Resolve odds ──────────────────────────────────────────────────────────

    # Try cache first
    cached = load_cache(cache_path)

    if cached is not None:
        game_odds = cached
        print("  [6b] Using cached odds — skipping API call")
    elif api_key:
        raw_games = fetch_odds(api_key, date_str)
        if raw_games:
            lookup   = parse_odds_to_lookup(raw_games)
            game_odds = build_game_odds_table(df, lookup)
            save_cache(game_odds, cache_path)
        else:
            print("  [6b] API returned no data — building fallback table")
            game_odds = build_game_odds_table(df, {})
    else:
        print("  [6b] WARNING: No API key provided and no cache found.")
        print("  [6b] Get a free key at https://the-odds-api.com")
        print("  [6b] Continuing with null odds (no adjustments applied)")
        game_odds = build_game_odds_table(df, {})

    # ── Merge odds onto slate ─────────────────────────────────────────────────

    # Drop any existing odds columns before merging
    existing_odds_cols = ["game_total","spread","implied_team_total",
                          "blowout_risk","low_total_flag","pace_tier","odds_source","ctx_adj"]
    df = df.drop(columns=[c for c in existing_odds_cols if c in df.columns], errors="ignore")

    df = df.merge(game_odds, on=["team", "opp_team"], how="left")

    # Combo / dual-team rows (e.g. team "PHI/DET") can miss the exact (team, opp_team)
    # pair in game_odds; backfill from any row that shares the same primary team code.
    miss_gt = df["game_total"].isna()
    if miss_gt.any():
        primary = (
            df["team"].astype(str).str.split("/").str[0].str.strip().str.upper()
        )
        base = df.loc[~df["game_total"].isna(), ["team", "game_total", "spread"]].copy()
        if not base.empty:
            base["_pt"] = base["team"].astype(str).str.split("/").str[0].str.strip().str.upper()
            gt_by_team = base.groupby("_pt")["game_total"].first()
            sp_by_team = base.groupby("_pt")["spread"].first()
            idx = df.index[miss_gt]
            df.loc[idx, "game_total"] = primary.loc[idx].map(gt_by_team)
            df.loc[idx, "spread"] = primary.loc[idx].map(sp_by_team)
            # Recompute derived fields where we filled totals
            filled = miss_gt & df["game_total"].notna()
            if filled.any():
                gt = pd.to_numeric(df.loc[filled, "game_total"], errors="coerce")
                sp = pd.to_numeric(df.loc[filled, "spread"], errors="coerce").fillna(0.0)
                df.loc[filled, "implied_team_total"] = (gt / 2.0 - sp / 2.0).round(1)
                df.loc[filled, "blowout_risk"] = sp.abs() > BLOWOUT_THRESHOLD
                df.loc[filled, "low_total_flag"] = gt < LOW_TOTAL_THRESH
                df.loc[filled, "pace_tier"] = gt.map(_pace_tier)
                df.loc[filled, "odds_source"] = "live"

    # Fill missing flags with safe defaults
    df["blowout_risk"]   = df["blowout_risk"].fillna(False).astype(bool)
    df["low_total_flag"] = df["low_total_flag"].fillna(False).astype(bool)
    df["odds_source"]    = df["odds_source"].fillna("fallback")

    # ── Compute context adjustment ────────────────────────────────────────────
    df["ctx_adj"] = df.apply(compute_ctx_adj, axis=1)

    # ── Summary ───────────────────────────────────────────────────────────────
    live_games  = (game_odds["odds_source"] == "live").sum()
    fallback    = (game_odds["odds_source"] == "fallback").sum()
    blowouts    = df["blowout_risk"].sum()
    low_totals  = df["low_total_flag"].sum()
    penalized   = (df["ctx_adj"] < 0).sum()

    print(f"\n  [6b] Odds coverage: {live_games} games live, {fallback} fallback")
    if live_games > 0:
        avg_total = game_odds[game_odds["game_total"].notna()]["game_total"].mean()
        print(f"  [6b] Avg game total: {avg_total:.1f}")
    print(f"  [6b] Blowout risk flags: {blowouts} props")
    print(f"  [6b] Low total flags:    {low_totals} props")
    print(f"  [6b] Props penalized:    {penalized} (ctx_adj < 0)")

    # Print game-level summary
    print(f"\n  [6b] Game Context Summary:")
    print(f"  {'Team':>5} vs {'Opp':<5}  {'Total':>6}  {'Spread':>7}  {'Impl':>6}  {'Blowout':>8}  {'LowTot':>7}")
    print(f"  {'-'*55}")
    for _, gr in game_odds.iterrows():
        gt  = f"{gr['game_total']:.1f}" if pd.notna(gr.get('game_total')) else "N/A"
        sp  = f"{gr['spread']:+.1f}"    if pd.notna(gr.get('spread'))     else "N/A"
        it  = f"{gr['implied_team_total']:.1f}" if pd.notna(gr.get('implied_team_total')) else "N/A"
        bl  = "⚠️ YES" if gr.get('blowout_risk') else "no"
        lt  = "⚠️ YES" if gr.get('low_total_flag') else "no"
        team = clean_abbr(str(gr['team']))
        opp  = clean_abbr(str(gr['opp_team']))
        print(f"  {team:>5} vs {opp:<5}  {gt:>6}  {sp:>7}  {it:>6}  {bl:>8}  {lt:>7}")

    # ── Save output ───────────────────────────────────────────────────────────
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"\n  [6b] ✅ Saved {len(df)} rows → {args.output}")
    print(f"  [6b] New columns: game_total, spread, implied_team_total, blowout_risk, low_total_flag, ctx_adj, odds_source\n")


if __name__ == "__main__":
    main()
