#!/usr/bin/env python3
"""
defense_report.py  (WNBA Pipeline)

Pulls WNBA team defensive stats from ESPN APIs and outputs:
  wnba_defense_summary.csv

Sources tried in order:
  1. site.api.espn.com  /teams/{id}/statistics   (splits → categories)
  2. cdn.espn.com       /core/wnba/standings      (opponent pts per game)
  3. site.api.espn.com  /summary scoreboard scan  (compute opp pts from box scores)

Defense ranking: dynamic N_TEAMS — quintiles → Elite / Above Avg / Avg / Below Avg / Weak

Run:
  py -3.14 defense_report.py --season 2026 --out wnba_defense_summary.csv
  py -3.14 defense_report.py --season 2026 --debug   (dumps raw stat keys)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from utils.defense_tiers import def_tier_from_overall_rank

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

TEAMS_URL      = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams"
TEAM_STATS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{team_id}/statistics"
STANDINGS_URL  = "https://cdn.espn.com/core/wnba/standings?xhr=1&season={season}"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?limit=200&dates={season}"


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, retries: int = 3, sleep: float = 0.6) -> dict:
    for attempt in range(1, retries + 1):
        try:
            time.sleep(sleep + random.uniform(0, 0.3))
            r = requests.get(url, headers=ESPN_HEADERS, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  [WARN] attempt {attempt}: {e}")
            time.sleep(2.0 * attempt)
    raise RuntimeError(f"Failed to fetch: {url}")


# ---------------------------------------------------------------------------
# Recursive JSON flattener — walks any nested dict/list and collects floats
# ---------------------------------------------------------------------------

def _flatten(obj: Any, prefix: str = "", out: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    if out is None:
        out = {}
    if isinstance(obj, dict):
        # ESPN pattern: {"name": "someKey", "value": 1.23}  or  {"displayValue": "..."}
        if "name" in obj and "value" in obj:
            key = f"{prefix}{str(obj['name']).lower().strip()}"
            try:
                out[key] = float(obj["value"])
            except (TypeError, ValueError):
                pass
        else:
            for k, v in obj.items():
                _flatten(v, f"{prefix}{k.lower()}_", out)
    elif isinstance(obj, list):
        for item in obj:
            _flatten(item, prefix, out)
    return out


# ---------------------------------------------------------------------------
# Team list
# ---------------------------------------------------------------------------

def get_teams() -> List[Dict[str, Any]]:
    data = _get(TEAMS_URL)
    teams = []
    for sport in (data.get("sports") or []):
        for league in (sport.get("leagues") or []):
            for team in (league.get("teams") or []):
                t = team.get("team") or {}
                teams.append({
                    "id":   str(t.get("id", "")),
                    "abbr": str(t.get("abbreviation", "")).upper(),
                    "name": str(t.get("displayName", "")),
                })
    return teams


# ---------------------------------------------------------------------------
# Source 1 — /teams/{id}/statistics
# ---------------------------------------------------------------------------

def get_team_stats_espn(team_id: str, debug: bool = False) -> Dict[str, float]:
    url  = TEAM_STATS_URL.format(team_id=team_id)
    data = _get(url)

    if debug:
        print(f"    [DEBUG] raw keys: {list(data.keys())}")

    stats: Dict[str, float] = {}

    # Walk every known nesting path ESPN uses
    paths = [
        data.get("results", {}).get("stats", {}).get("splits", {}),
        data.get("splits", {}),
        data,
    ]
    for root in paths:
        for cat in (root.get("categories") or []):
            cat_name = str(cat.get("name", "")).lower()
            for stat in (cat.get("stats") or []):
                key = f"{cat_name}_{str(stat.get('name','')).lower().strip()}"
                try:
                    stats[key] = float(stat.get("value"))
                except (TypeError, ValueError):
                    pass

    # Fallback: deep recursive flatten of full response
    if not stats:
        stats = _flatten(data)

    if debug and stats:
        print(f"    [DEBUG] parsed keys (first 30): {list(stats.keys())[:30]}")

    return stats


# ---------------------------------------------------------------------------
# Source 2 — CDN standings (has oppPointsPerGame reliably)
# ---------------------------------------------------------------------------

def get_standings_defense(season: str) -> Dict[str, Dict[str, float]]:
    """
    Returns {abbr_upper: {"OPP_PPG": float, "W": float, "L": float}}
    """
    result: Dict[str, Dict[str, float]] = {}
    try:
        url  = STANDINGS_URL.format(season=season)
        data = _get(url)
        # Navigate into standings entries
        content = data.get("content", data)
        standings = content.get("standings", data.get("standings", {}))
        groups = standings.get("groups") or standings.get("entries") or []

        # ESPN standings: groups → entries → team + stats
        def walk_groups(groups_list):
            for g in groups_list:
                for entry in (g.get("entries") or []):
                    team = entry.get("team", {})
                    abbr = str(team.get("abbreviation", "")).upper()
                    if not abbr:
                        continue
                    row: Dict[str, float] = {}
                    for stat in (entry.get("stats") or []):
                        name = str(stat.get("name", "")).lower()
                        try:
                            val = float(stat.get("value", ""))
                        except (TypeError, ValueError):
                            continue
                        if "opp" in name and "point" in name:
                            row["OPP_PPG"] = val
                        elif name in ("wins", "w"):
                            row["W"] = val
                        elif name in ("losses", "l"):
                            row["L"] = val
                        elif "pointspergame" in name or name == "ppg":
                            row.setdefault("PPG", val)
                    if row:
                        result[abbr] = row
                # recurse sub-groups
                walk_groups(g.get("groups") or [])

        walk_groups(groups)

        if not result:
            # Brute-force flatten entire standings blob looking for opp pts
            flat = _flatten(data)
            if flat:
                print("  [INFO] standings flatten found keys:", list(flat.keys())[:15])

    except Exception as e:
        print(f"  [WARN] standings fetch failed: {e}")

    return result


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------

def rank_series(s: pd.Series, ascending: bool) -> pd.Series:
    return s.rank(method="min", ascending=ascending).astype("Int64")


def tier_from_rank(r, n_teams: int) -> str:
    return def_tier_from_overall_rank(r, n_teams)


# ---------------------------------------------------------------------------
# Column finder (broader patterns)
# ---------------------------------------------------------------------------

DEF_PATTERNS = [
    # opponent points
    "opponentpointspergame", "opp_pointspergame", "oppptspergame",
    "opp_pts", "opponent_pts", "pointsallowed", "points_allowed",
    "opposingpoints", "opp_ppg",
    # defensive rating
    "defensiverating", "def_rating", "defrtg", "defensive_rating",
    # steals, blocks (secondary)
    "steals", "blocks",
    # turnovers forced
    "opponentturnovers",
]


def find_col(df: pd.DataFrame, patterns: List[str]) -> str:
    for p in patterns:
        matches = [c for c in df.columns if p in c.lower().replace(" ", "").replace("_", "")]
        if matches:
            return matches[0]
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026")
    ap.add_argument("--out",    default="wnba_defense_summary.csv")
    ap.add_argument("--sleep",  type=float, default=0.6)
    ap.add_argument("--debug",  action="store_true", help="Print raw stat keys")
    args = ap.parse_args()

    # --- Teams ---
    print("→ Fetching WNBA teams from ESPN...")
    teams = get_teams()
    if not teams:
        print("❌ No teams returned")
        return
    print(f"  Found {len(teams)} teams")

    # --- Per-team stats (Source 1) ---
    rows = []
    for t in teams:
        print(f"  Fetching stats: {t['abbr']} ({t['id']})")
        try:
            stats = get_team_stats_espn(t["id"], debug=args.debug)
        except Exception as e:
            print(f"    WARN: {e}")
            stats = {}

        row = {
            "TEAM_ABBREVIATION": t["abbr"],
            "TEAM_NAME":         t["name"],
            "TEAM_ID":           t["id"],
        }
        row.update(stats)
        rows.append(row)
        time.sleep(args.sleep)

    df = pd.DataFrame(rows)

    # Print all available stat columns so you can see what ESPN returned
    stat_cols = [c for c in df.columns if c not in ("TEAM_ABBREVIATION", "TEAM_NAME", "TEAM_ID")]
    if stat_cols:
        print(f"\n  [INFO] ESPN stat columns found ({len(stat_cols)}): {stat_cols[:40]}")
    else:
        print("\n  [WARN] ESPN /statistics endpoint returned no parseable stats")

    # --- Standings merge (Source 2) ---
    print(f"\n→ Fetching WNBA standings for season {args.season}...")
    standings = get_standings_defense(args.season)
    if standings:
        print(f"  Got standings for {len(standings)} teams: {list(standings.keys())}")
        st_df = pd.DataFrame.from_dict(standings, orient="index").reset_index()
        st_df.rename(columns={"index": "TEAM_ABBREVIATION"}, inplace=True)
        df = df.merge(st_df, on="TEAM_ABBREVIATION", how="left")
    else:
        print("  [WARN] No standings data — will rely solely on /statistics endpoint")

    # --- Identify best defensive column ---
    n_teams = len(df)

    # Priority: OPP_PPG from standings, then search stat cols
    rank_cols = []

    if "OPP_PPG" in df.columns and df["OPP_PPG"].notna().sum() >= 3:
        df["OPP_PPG"] = pd.to_numeric(df["OPP_PPG"], errors="coerce")
        df["OPP_PTS_RANK"] = rank_series(df["OPP_PPG"], ascending=True)  # lower = better defense
        rank_cols.append("OPP_PTS_RANK")
        print(f"  ✓ Using OPP_PPG (opponent points per game) as primary metric")

    pts_col = find_col(df, DEF_PATTERNS[:9])
    if pts_col and pts_col not in ("OPP_PPG",) and df[pts_col].notna().sum() >= 3:
        df[pts_col] = pd.to_numeric(df[pts_col], errors="coerce")
        df["ESPN_PTS_RANK"] = rank_series(df[pts_col], ascending=True)
        rank_cols.append("ESPN_PTS_RANK")
        print(f"  ✓ Using ESPN stat col: {pts_col}")

    rating_col = find_col(df, DEF_PATTERNS[9:12])
    if rating_col and df[rating_col].notna().sum() >= 3:
        df[rating_col] = pd.to_numeric(df[rating_col], errors="coerce")
        df["DEF_RATING_RANK"] = rank_series(df[rating_col], ascending=True)
        rank_cols.append("DEF_RATING_RANK")
        print(f"  ✓ Using defensive rating col: {rating_col}")

    if rank_cols:
        df["OVERALL_DEF_SCORE"] = df[[c for c in rank_cols]].mean(axis=1)
        df["OVERALL_DEF_RANK"]  = rank_series(df["OVERALL_DEF_SCORE"], ascending=True)
        print(f"\n  → Composite rank from: {rank_cols}")
    else:
        print("\n⚠️  No defensive metrics found after all sources.")
        print("     Dumping ALL available columns so you can identify the right key:")
        print(f"     {list(df.columns)}")
        if args.debug:
            # Save raw data for inspection
            raw_path = args.out.replace(".csv", "_raw_debug.csv")
            df.to_csv(raw_path, index=False)
            print(f"     Raw data saved to {raw_path}")
        df["OVERALL_DEF_SCORE"] = np.nan
        df["OVERALL_DEF_RANK"]  = pd.Series(range(1, len(df) + 1), index=df.index, dtype="Int64")

    df["DEF_TIER"] = df["OVERALL_DEF_RANK"].apply(lambda r: tier_from_rank(r, n_teams))

    # Select clean output columns
    out_cols = ["TEAM_ABBREVIATION", "TEAM_NAME", "TEAM_ID"]
    for c in ["OPP_PPG", pts_col, rating_col, "OPP_PTS_RANK", "ESPN_PTS_RANK",
              "DEF_RATING_RANK", "OVERALL_DEF_SCORE", "OVERALL_DEF_RANK", "DEF_TIER"]:
        if c and c in df.columns and c not in out_cols:
            out_cols.append(c)

    df[out_cols].to_csv(args.out, index=False)
    print(f"\n✅ Saved → {args.out}  (teams={len(df)})")

    display_cols = [c for c in ["TEAM_ABBREVIATION", "OPP_PPG", pts_col, "OVERALL_DEF_RANK", "DEF_TIER"]
                    if c and c in df.columns]
    print(df[display_cols].sort_values("OVERALL_DEF_RANK").to_string(index=False))

    # ── Write to proporacle_ref.db ───────────────────────────────────────────────
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "scripts"))
        from defense_db import write_defense_to_db
        df_db = df[out_cols].copy()
        # WNBA uses TEAM_ABBREVIATION as key — map to 'team'
        if "TEAM_ABBREVIATION" in df_db.columns:
            df_db["team"] = df_db["TEAM_ABBREVIATION"].astype(str).str.strip().str.upper()
        write_defense_to_db(df_db, sport="wnba")
    except Exception as _e:
        print(f"  ⚠️  Could not write to DB: {_e}")


if __name__ == "__main__":
    main()
