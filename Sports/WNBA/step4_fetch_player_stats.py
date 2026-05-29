#!/usr/bin/env python3
"""
step4_fetch_player_stats.py  (WNBA Pipeline)

Fetches WNBA player stats from ESPN Site API and attaches rolling
game windows (stat_g1..stat_g10), last5/last10/season averages.

ESPN paths used:
  Scoreboard: site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={yyyymmdd}
  Summary:    site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event={event_id}

Props supported (same as NBA):
  pts, reb, ast, stl, blk, tov, stocks, pra, pr, pa, ra, fantasy,
  fgm, fga, fg3m, fg3a, fg2m, fg2a, ftm, fta

Output adds (per row):
  stat_g1..stat_g10    — rolling game values (most recent = g1)
  stat_last5_avg       — average of g1..g5
  stat_last10_avg      — average of g1..g10
  stat_season_avg      — season average from ESPN
  line_hit_rate_over_ou_5, line_hit_rate_under_ou_5
  line_hit_rate_over_ou_10, line_hit_rate_under_ou_10
  last5_over, last5_under, last5_push, last5_hit_rate
  unsupported_prop, unsupported_reason

Rolling windows can include the previous WNBA season (e.g. 2025 rows when --season is 2026)
so early-season L5/ L10 are not starved. Use --no-include-prior-season-stats to disable.

Run:
  py -3.14 step4_fetch_player_stats.py \
      --slate step3_wnba_defense.csv \
      --out   step4_wnba_stats.csv \
      --date  2026-07-15 \
      --days  35 \
      --cache wnba_espn_cache.csv \
      --sleep 0.8
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set, Tuple

import numpy as np
import pandas as pd
import requests

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[2]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import ensure_wnba_schema, log_pipeline_health, open_db, upsert_rows
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={yyyymmdd}"
SUMMARY_URL    = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary?event={event_id}"

ALLSTAR_BREAKS: List[Tuple[str,str]] = [
    # Add WNBA All-Star break dates each season here
    # ("2026-07-18", "2026-07-20"),
]

WNBA_TEAM_KEY_MAP = {
    # slate abbrev -> DB abbrev
    "LAS": "LA",     # Los Angeles Sparks
    "LVA": "LV",     # Las Vegas Aces
    "NYL": "NY",     # New York Liberty
    "CON": "CON",    # Connecticut Sun (already matches)
    "DAL": "DAL",    # Dallas Wings
    "IND": "IND",    # Indiana Fever
    "PHX": "PHX",    # Phoenix Mercury
    "SEA": "SEA",    # Seattle Storm
    "CHI": "CHI",    # Chicago Sky
    "ATL": "ATL",    # Atlanta Dream
    "MIN": "MIN",    # Minnesota Lynx
    "WSH": "WSH",    # Washington Mystics
    "POR": "POR",    # Portland (if present)
    "GS": "GS",      # Golden State
}


def _parse_slate_game_date(row: pd.Series) -> str:
    for col in ("game_date", "game_start", "start_time", "fetched_at"):
        raw = str(row.get(col, "") or "").strip()
        if not raw:
            continue
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
    return ""


def compute_rest_days(con, team: str, game_date: str, table: str = "wnba") -> int:
    team = str(team or "").strip().upper()
    game_date = str(game_date or "").strip()
    if len(game_date) >= 10:
        game_date = game_date[:10]
    if not team or len(game_date) < 10:
        return -1
    try:
        prev = con.execute(
            f"SELECT MAX(game_date) FROM {table} WHERE team = ? AND game_date < ?",
            (team, game_date),
        ).fetchone()
        prev_date = prev[0] if prev and prev[0] else None
        if not prev_date:
            return -1
        days = (
            datetime.strptime(game_date, "%Y-%m-%d")
            - datetime.strptime(str(prev_date)[:10], "%Y-%m-%d")
        ).days
        return int(days)
    except Exception:
        return -1


def _wnba_team_keys_align(con, slate: pd.DataFrame) -> bool:
    if "team" not in slate.columns:
        return False
    slate_teams: Set[str] = set()
    for t in slate["team"].astype(str).str.strip().str.upper().unique():
        if not t or "/" in t:
            continue
        slate_teams.add(t)
    try:
        rows = con.execute(
            "SELECT DISTINCT team FROM wnba WHERE team IS NOT NULL AND team != '' "
            "ORDER BY team LIMIT 20"
        ).fetchall()
        db_sample = [str(r[0]).strip().upper() for r in rows if r and r[0]]
    except Exception:
        db_sample = []
    print(f"[B2B] WNBA DB teams (first 20): {db_sample}")
    print(f"[B2B] WNBA slate teams (sample): {sorted(slate_teams)[:20]}")
    try:
        db_all = {
            str(r[0]).strip().upper()
            for r in con.execute(
                "SELECT DISTINCT team FROM wnba WHERE team IS NOT NULL AND team != ''"
            ).fetchall()
            if r and r[0]
        }
    except Exception:
        db_all = set()
    if not slate_teams:
        return False
    for t in slate_teams:
        if WNBA_TEAM_KEY_MAP.get(t, t) not in db_all:
            return False
    return True


def attach_b2b_columns(
    df: pd.DataFrame, con, table: str = "wnba", sport_label: str = "WNBA", enabled: bool = True
) -> pd.DataFrame:
    out = df.copy()
    out["days_rest"] = -1
    out["is_back_to_back"] = 0
    out["opp_days_rest"] = -1
    out["opp_b2b"] = 0
    if not enabled:
        # TODO: WNBA team key mismatch — verify wnba DB table contains club-level rows matching slate abbreviations.
        print(f"[B2B] {sport_label}: {len(out)} rows, 0 back-to-backs found (team key mismatch; days_rest=-1)")
        return out
    if "team" not in out.columns:
        print(f"[B2B] {sport_label}: 0 rows, 0 back-to-backs found (no team column)")
        return out

    game_dates = out.apply(_parse_slate_game_date, axis=1)
    rest_cache: dict[tuple[str, str], int] = {}

    def _lookup(team_val: str, gd: str) -> int:
        raw_team = str(team_val or "").strip().upper()
        gd_s = str(gd or "").strip()[:10]
        if not raw_team or len(gd_s) < 10 or "/" in raw_team:
            return -1
        db_team = WNBA_TEAM_KEY_MAP.get(raw_team, raw_team)
        key = (db_team, gd_s)
        if key not in rest_cache:
            rest_cache[key] = compute_rest_days(con, db_team, gd_s, table=table)
        return rest_cache[key]

    out["days_rest"] = [_lookup(out.at[i, "team"], game_dates.at[i]) for i in out.index]
    out["is_back_to_back"] = (pd.to_numeric(out["days_rest"], errors="coerce") == 1).astype(int)
    if "opp_team" in out.columns:
        out["opp_days_rest"] = [_lookup(out.at[i, "opp_team"], game_dates.at[i]) for i in out.index]
        out["opp_b2b"] = (pd.to_numeric(out["opp_days_rest"], errors="coerce") == 1).astype(int)
    b2b_n = int((out["is_back_to_back"] == 1).sum())
    print(f"[B2B] {sport_label}: {len(out)} rows, {b2b_n} back-to-backs found")
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _sleep(base: float, jitter: float = 0.8) -> None:
    time.sleep(max(0.0, base + random.uniform(0, jitter)))


def _norm_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(p for p in s.split() if p not in {"jr","sr","ii","iii","iv","v"})


def _to_float(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _parse_made_att(s: str) -> Tuple[float, float]:
    txt = str(s or "").strip()
    if not txt or txt == "--":
        return (np.nan, np.nan)
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt)
    if not m:
        return (np.nan, np.nan)
    return float(m.group(1)), float(m.group(2))


def _parse_minutes(s: str) -> float:
    txt = str(s or "").strip()
    if not txt or txt == "--":
        return np.nan
    if ":" in txt:
        parts = txt.split(":")
        try:
            return int(parts[0]) + int(parts[1]) / 60.0
        except (ValueError, IndexError):
            return np.nan
    return pd.to_numeric(txt, errors="coerce")


def _minutes_series(df: pd.DataFrame) -> pd.Series:
    if df.empty or "MIN" not in df.columns:
        return pd.Series([np.nan] * len(df), index=df.index)
    raw = df["MIN"]
    if pd.api.types.is_numeric_dtype(raw):
        return pd.to_numeric(raw, errors="coerce")
    return raw.map(_parse_minutes)


def filter_games_by_minutes(df: pd.DataFrame, min_minutes: float) -> pd.DataFrame:
    """Optionally drop low-minute outings before rolling L5/L10 (--min-minutes-rolling > 0)."""
    if df.empty or min_minutes <= 0:
        return df
    mins = _minutes_series(df)
    keep = mins.notna() & (mins >= float(min_minutes))
    return df.loc[keep].copy()


def _norm_stat_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k or "").lower())


def _build_stat_map(raw_keys: List[str], raw_stats: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in zip(raw_keys, raw_stats):
        kk = _norm_stat_key(k)
        if kk:
            out[kk] = str(v)
    return out


def _first_stat(stat_map: Dict[str, str], aliases: List[str]) -> str:
    for a in aliases:
        aa = _norm_stat_key(a)
        if aa in stat_map:
            return stat_map[aa]
    return ""


def _is_allstar(dt: datetime) -> bool:
    d = dt.strftime("%Y-%m-%d")
    for start, end in ALLSTAR_BREAKS:
        if start <= d <= end:
            return True
    return False


# ── ESPN API ──────────────────────────────────────────────────────────────────

def espn_get(url: str, timeout: float, retries: int, sleep_s: float) -> dict:
    for attempt in range(1, retries + 1):
        try:
            _sleep(sleep_s, 0.5)
            r = requests.get(url, headers=ESPN_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            backoff = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(0.5, 2.0)
            print(f"  [WARN] ESPN attempt {attempt}/{retries}: {type(e).__name__} — retry in {backoff:.1f}s")
            time.sleep(backoff)
    raise RuntimeError(f"ESPN GET failed: {url}")


def fetch_event_ids(date_yyyymmdd: str, timeout: float, retries: int, sleep_s: float) -> List[str]:
    url  = SCOREBOARD_URL.format(yyyymmdd=date_yyyymmdd)
    data = espn_get(url, timeout, retries, sleep_s)
    seen, out = set(), []
    for ev in (data.get("events") or []):
        eid = str(ev.get("id","")).strip()
        if eid and eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def parse_boxscore(summary: dict, scoreboard_date: str = "") -> pd.DataFrame:
    """Parse full-game WNBA boxscore → one row per player per game."""
    box   = (summary or {}).get("boxscore") or {}
    rows  = []

    game_date = str(scoreboard_date or "").strip()[:10]
    header    = (summary or {}).get("header") or {}
    comp      = header.get("competitions") or []
    if not game_date and comp:
        gd = comp[0].get("date")
        if gd:
            game_date = str(gd)[:10]

    event_id = ""
    gn = (summary or {}).get("gamepackageJSON") or {}
    hdr2 = gn.get("header") or {}
    for comp2 in (hdr2.get("competitions") or []):
        event_id = str(comp2.get("id","")).strip()
        if event_id:
            break

    for team_block in (box.get("players") or []):
        team_abbr = ""
        ti = team_block.get("team") or {}
        team_abbr = str(ti.get("abbreviation","")).strip().upper()

        stats_block = team_block.get("statistics") or []
        if not stats_block:
            continue
        sb = stats_block[0]

        keys = [str(k).upper() for k in (sb.get("keys") or sb.get("names") or sb.get("columns") or [])]
        for ath in (sb.get("athletes") or []):
            ath_info  = ath.get("athlete") or {}
            ath_id    = str(ath_info.get("id","")).strip()
            ath_name  = str(ath_info.get("displayName", ath_info.get("shortName",""))).strip()
            ath_norm  = _norm_name(ath_name)
            did_not_play = bool(ath.get("didNotPlay"))

            raw_stats = ath.get("stats") or []
            if not raw_stats or did_not_play:
                continue

            stat_map = _build_stat_map(keys, raw_stats)

            # Parse shooting: ESPN returns "FGM-FGA", "3PM-3PA", "FTM-FTA"
            fgm, fga = _parse_made_att(
                _first_stat(stat_map, ["FG", "FGM-FGA", "fieldGoalsMade-fieldGoalsAttempted"])
            )
            fg3m, fg3a = _parse_made_att(
                _first_stat(stat_map, ["3PT", "3PM-3PA", "threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
            )
            ftm, fta = _parse_made_att(
                _first_stat(stat_map, ["FT", "FTM-FTA", "freeThrowsMade-freeThrowsAttempted"])
            )
            fg2m = (fgm - fg3m) if not (np.isnan(fgm) or np.isnan(fg3m)) else np.nan
            fg2a = (fga - fg3a) if not (np.isnan(fga) or np.isnan(fg3a)) else np.nan

            row_out = {
                "game_date":        game_date,
                "event_id":         event_id,
                "ESPN_ATHLETE_ID":  ath_id,
                "PLAYER_NAME":      ath_name,
                "PLAYER_NORM":      ath_norm,
                "TEAM":             team_abbr,
                "MIN":              _parse_minutes(_first_stat(stat_map, ["MIN", "minutes"])),
                "PTS":              pd.to_numeric(_first_stat(stat_map, ["PTS", "points"]), errors="coerce"),
                "REB":              pd.to_numeric(_first_stat(stat_map, ["REB", "rebounds", "DREB", "defensiveRebounds"]), errors="coerce"),
                "AST":              pd.to_numeric(_first_stat(stat_map, ["AST", "assists"]), errors="coerce"),
                "STL":              pd.to_numeric(_first_stat(stat_map, ["STL", "steals"]), errors="coerce"),
                "BLK":              pd.to_numeric(_first_stat(stat_map, ["BLK", "blocks"]), errors="coerce"),
                "TO":               pd.to_numeric(_first_stat(stat_map, ["TO", "TOV", "turnovers"]), errors="coerce"),
                "FGM":              fgm,  "FGA":  fga,
                "FG3M":             fg3m, "FG3A": fg3a,
                "FG2M":             fg2m, "FG2A": fg2a,
                "FTM":              ftm,  "FTA":  fta,
                "SEASON":           "",
            }

            # ── Bouncer: reject impossible or junk player rows ────────────────
            def _bad_num(x) -> bool:
                if x is None:
                    return False
                if isinstance(x, float) and np.isnan(x):
                    return False
                try:
                    return float(x) < 0
                except Exception:
                    return True

            # Negative checks (core rule)
            if any(_bad_num(row_out.get(k)) for k in ["MIN","PTS","REB","AST","STL","BLK","TO","FGM","FGA","FG3M","FG3A","FG2M","FG2A","FTM","FTA"]):
                continue

            # Plausibility bounds (keep generous to avoid false rejects)
            mins = row_out.get("MIN")
            pts  = row_out.get("PTS")
            reb  = row_out.get("REB")
            ast  = row_out.get("AST")
            stl  = row_out.get("STL")
            blk  = row_out.get("BLK")
            tov  = row_out.get("TO")
            fgm_v, fga_v = row_out.get("FGM"), row_out.get("FGA")
            fg3m_v, fg3a_v = row_out.get("FG3M"), row_out.get("FG3A")
            ftm_v, fta_v = row_out.get("FTM"), row_out.get("FTA")

            try:
                if mins is not None and not (isinstance(mins, float) and np.isnan(mins)) and float(mins) > 60:
                    continue
                for v, cap in [(pts, 120), (reb, 60), (ast, 40), (stl, 20), (blk, 20), (tov, 30)]:
                    if v is not None and not (isinstance(v, float) and np.isnan(v)) and float(v) > cap:
                        raise ValueError("cap")
                if fgm_v is not None and fga_v is not None and not (np.isnan(fgm_v) or np.isnan(fga_v)) and float(fgm_v) > float(fga_v):
                    continue
                if fg3m_v is not None and fg3a_v is not None and not (np.isnan(fg3m_v) or np.isnan(fg3a_v)) and float(fg3m_v) > float(fg3a_v):
                    continue
                if ftm_v is not None and fta_v is not None and not (np.isnan(ftm_v) or np.isnan(fta_v)) and float(ftm_v) > float(fta_v):
                    continue
            except Exception:
                continue

            rows.append(row_out)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── stat derivation ───────────────────────────────────────────────────────────

def resolve_prop_slug(row: pd.Series) -> str:
    """Best-effort prop key for derive_stat (prop_norm, else normalized prop_type)."""
    raw = str(row.get("prop_norm", "") or row.get("prop_type", "")).strip().lower()
    if not raw:
        return ""
    # Quick normalize (subset of step2 norm_prop) when slate still has PP labels.
    clean = raw.replace(" ", "").replace("-", "").replace("_", "")
    alias = {
        "points": "pts", "rebounds": "reb", "assists": "ast", "blocks": "blk",
        "blockedshots": "blk", "steals": "stl", "turnovers": "tov",
        "blks+stls": "stocks", "fantasyscore": "fantasy",
        "pts+rebs+asts": "pra", "points+rebounds+assists": "pra",
        "pts+rebs": "pr", "points+rebounds": "pr",
        "pts+asts": "pa", "points+assists": "pa",
        "rebs+asts": "ra", "rebounds+assists": "ra",
        "fgm": "fgm", "fgmade": "fgm", "fga": "fga", "fgattempted": "fga",
        "fieldgoalsmade": "fgm", "fieldgoalsattempted": "fga",
        "3ptfgmade": "fg3m", "3ptfgattempted": "fg3a", "3ptmade": "fg3m",
        "3ptattempted": "fg3a", "threepointersmade": "fg3m",
        "threepointersattempted": "fg3a", "3pointersmade": "fg3m",
        "3pointersattempted": "fg3a", "2ptfgmade": "fg2m", "2ptfgattempted": "fg2a",
        "2ptmade": "fg2m", "2ptattempted": "fg2a", "twopointersmade": "fg2m",
        "twopointersattempted": "fg2a", "ftm": "ftm", "ftmade": "ftm",
        "fta": "fta", "ftattempted": "fta", "freethrowsmade": "ftm",
        "freethrowsattempted": "fta",
    }
    return alias.get(clean, clean)


def derive_stat(df: pd.DataFrame, prop_norm: str) -> pd.Series:
    p = re.sub(r"\(combo\)\s*$", "", (prop_norm or "").lower().strip()).strip()

    pts  = _to_float(df.get("PTS",  pd.Series([np.nan]*len(df), index=df.index)))
    reb  = _to_float(df.get("REB",  pd.Series([np.nan]*len(df), index=df.index)))
    ast  = _to_float(df.get("AST",  pd.Series([np.nan]*len(df), index=df.index)))
    stl  = _to_float(df.get("STL",  pd.Series([np.nan]*len(df), index=df.index)))
    blk  = _to_float(df.get("BLK",  pd.Series([np.nan]*len(df), index=df.index)))
    tov  = _to_float(df.get("TO",   pd.Series([np.nan]*len(df), index=df.index)))
    fga  = _to_float(df.get("FGA",  pd.Series([np.nan]*len(df), index=df.index)))
    fgm  = _to_float(df.get("FGM",  pd.Series([np.nan]*len(df), index=df.index)))
    fg3a = _to_float(df.get("FG3A", pd.Series([np.nan]*len(df), index=df.index)))
    fg3m = _to_float(df.get("FG3M", pd.Series([np.nan]*len(df), index=df.index)))
    fta  = _to_float(df.get("FTA",  pd.Series([np.nan]*len(df), index=df.index)))
    ftm  = _to_float(df.get("FTM",  pd.Series([np.nan]*len(df), index=df.index)))
    fg2a = fga - fg3a
    fg2m = fgm - fg3m

    if p in ("pts","points"):           return pts
    if p in ("reb","rebounds"):         return reb
    if p in ("ast","assists"):          return ast
    if p == "pra":                      return pts + reb + ast
    if p == "pr":                       return pts + reb
    if p == "pa":                       return pts + ast
    if p == "ra":                       return reb + ast
    if p == "stocks":                   return stl + blk
    if p in ("stl","steals"):           return stl
    if p in ("blk","blocks"):           return blk
    if p in ("tov","turnovers","to"):   return tov
    if p in ("fga","fieldgoalsattempted"): return fga
    if p in ("fgm","fieldgoalsmade"): return fgm
    if p in ("fg3a","3pta","3ptattempted","threepointersattempted","3pointersattempted"): return fg3a
    if p in ("fg3m","3ptm","3ptmade","3pt made","3pm","threepointersmade","3pointersmade"): return fg3m
    if p in ("fg2a","2pta","2ptattempted","twopointersattempted"): return fg2a
    if p in ("fg2m","2ptm","2ptmade","twopointersmade"): return fg2m
    if p in ("fta","freethrowsattempted"): return fta
    if p in ("ftm","freethrowsmade"):   return ftm
    if p in ("fantasy","fantasy_score"):
        return pts + 1.2*reb + 1.5*ast + 3.0*stl + 3.0*blk - tov
    return pd.Series([np.nan]*len(df), index=df.index)


def calc_hit_context(vals: List[float], line: float, k: int = 5) -> Tuple[int,int,int,float,float,float]:
    over = under = push = 0
    for v in vals[:k]:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if v > line:   over  += 1
        elif v < line: under += 1
        else:          push  += 1
    total_all = over + under + push
    total_ou  = over + under
    hr_all  = (over / total_all) if total_all else np.nan
    hr_ou   = (over / total_ou)  if total_ou  else np.nan
    ur_ou   = (under / total_ou) if total_ou  else np.nan
    return over, under, push, hr_all, hr_ou, ur_ou


def _lookup_espn_id(player_name: str, name_to_id: Dict[str, str]) -> str:
    p_norm = _norm_name(player_name)
    if p_norm in name_to_id:
        return str(name_to_id[p_norm])
    # Last-resort: unique last-token match when slate spelling differs slightly.
    parts = p_norm.split()
    if not parts:
        return ""
    last = parts[-1]
    hits = [v for k, v in name_to_id.items() if k.split()[-1:] == [last]]
    return str(hits[0]) if len(hits) == 1 else ""


def _player_games_for_athlete(
    cache_filt: pd.DataFrame, ath_id: str, min_minutes: float
) -> pd.DataFrame:
    player_games = cache_filt[cache_filt["ESPN_ATHLETE_ID"].astype(str) == str(ath_id)].copy()
    if player_games.empty:
        return player_games
    player_games = player_games.sort_values("game_date", ascending=False)
    return filter_games_by_minutes(player_games, float(min_minutes))


def _stat_values_for_athlete(
    cache_filt: pd.DataFrame,
    ath_id: str,
    prop_n: str,
    n: int,
    min_minutes: float,
) -> Tuple[List[float], pd.DataFrame]:
    player_games = _player_games_for_athlete(cache_filt, ath_id, min_minutes)
    if player_games.empty:
        return [], player_games
    stat_series = derive_stat(player_games, prop_n)
    if stat_series.isna().all():
        return [], player_games
    vals = [float(v) if not pd.isna(v) else np.nan for v in stat_series.tolist()][:n]
    return vals, player_games


def _combo_stat_values(
    cache_filt: pd.DataFrame,
    ath_ids: List[str],
    prop_n: str,
    n: int,
    min_minutes: float,
) -> Tuple[List[float], List[pd.DataFrame]]:
    """
    Sum stats across combo legs, aligned by recent-game index (same as NBA get_vals_combo).
    Only counts games where every leg has a value at that index.
    """
    per_player: List[List[float]] = []
    game_frames: List[pd.DataFrame] = []
    for ath_id in ath_ids:
        vals, games = _stat_values_for_athlete(cache_filt, ath_id, prop_n, n, min_minutes)
        if not vals:
            return [], game_frames
        per_player.append(vals)
        game_frames.append(games)
    min_games = min(len(v) for v in per_player)
    if min_games <= 0:
        return [], game_frames
    summed = [
        float(sum(v[i] for v in per_player))
        for i in range(min_games)
    ]
    while len(summed) < n:
        summed.append(np.nan)
    return summed[:n], game_frames


def _append_empty_stat_row(new_cols: Dict[str, List], *, reason: str = "") -> None:
    for k in new_cols:
        new_cols[k].append(np.nan if "rate" in k or "avg" in k else ("" if k == "unsupported_reason" else np.nan))
    new_cols["unsupported_prop"][-1] = 0
    new_cols["unsupported_reason"][-1] = reason
    new_cols["espn_athlete_id"][-1] = ""


def _append_stat_row(
    new_cols: Dict[str, List],
    vals_mr: List[float],
    line_val: float,
    n: int,
    ath_id: str,
    player_games: pd.DataFrame,
) -> None:
    for i in range(n):
        new_cols[f"stat_g{i+1}"].append(vals_mr[i] if i < len(vals_mr) else np.nan)

    valid_vals = [v for v in vals_mr if not (isinstance(v, float) and np.isnan(v))]
    new_cols["stat_last5_avg"].append(float(np.mean(valid_vals[:5])) if valid_vals[:5] else np.nan)
    new_cols["stat_last10_avg"].append(float(np.mean(valid_vals[:10])) if valid_vals[:10] else np.nan)

    if not player_games.empty and "SEASON_AVG" in player_games.columns:
        sv = pd.to_numeric(player_games["SEASON_AVG"], errors="coerce").dropna()
        new_cols["stat_season_avg"].append(float(sv.mean()) if len(sv) else np.nan)
    else:
        new_cols["stat_season_avg"].append(float(np.mean(valid_vals)) if valid_vals else np.nan)

    if not np.isnan(line_val):
        o5, u5, p5, hr5, hr5_ou, ur5_ou = calc_hit_context(vals_mr, line_val, 5)
        _o10, _u10, _p10, _hr10, hr10_ou, ur10_ou = calc_hit_context(vals_mr, line_val, 10)
        new_cols["last5_over"].append(o5)
        new_cols["last5_under"].append(u5)
        new_cols["last5_push"].append(p5)
        new_cols["last5_hit_rate"].append(hr5)
        new_cols["line_hit_rate_over_ou_5"].append(hr5_ou)
        new_cols["line_hit_rate_under_ou_5"].append(ur5_ou)
        new_cols["line_hit_rate_over_ou_10"].append(hr10_ou)
        new_cols["line_hit_rate_under_ou_10"].append(ur10_ou)
    else:
        for k in [
            "last5_over", "last5_under", "last5_push", "last5_hit_rate",
            "line_hit_rate_over_ou_5", "line_hit_rate_under_ou_5",
            "line_hit_rate_over_ou_10", "line_hit_rate_under_ou_10",
        ]:
            new_cols[k].append(np.nan)

    new_cols["unsupported_prop"].append(0)
    new_cols["unsupported_reason"].append("")
    new_cols["espn_athlete_id"].append(ath_id)


def _is_combo_row(row: pd.Series) -> bool:
    if pd.to_numeric(row.get("is_combo_player", 0), errors="coerce") == 1:
        return True
    return "+" in str(row.get("player", ""))


def split_combo_name(player: str) -> Tuple[str, str]:
    parts = [p.strip() for p in str(player or "").split("+")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (str(player).strip(), "")


def find_incomplete_wnba_events(cache: pd.DataFrame, *, min_team_minutes_sum: float = 300.0) -> set[str]:
    """
    Events to re-fetch: missing PTS or clearly partial boxscores (in-game snapshot cached early).
    A finished WNBA game typically has 300+ total player-minutes across both rosters.
    """
    if cache.empty or "event_id" not in cache.columns:
        return set()
    incomplete: set[str] = set()
    eid = cache["event_id"].astype(str)
    if "PTS" in cache.columns:
        pts = pd.to_numeric(cache["PTS"], errors="coerce")
        grouped = cache.assign(_pts=pts).groupby(eid)["_pts"].apply(lambda s: int(s.notna().sum()))
        incomplete |= set(grouped[grouped <= 0].index.tolist())
    if "MIN" in cache.columns:
        mins = _minutes_series(cache)

        def _event_minutes_sum(g: pd.DataFrame) -> float:
            return float(pd.to_numeric(_minutes_series(g), errors="coerce").sum())

        by_event = cache.groupby(eid, group_keys=False).apply(_event_minutes_sum)
        incomplete |= set(by_event[by_event < float(min_team_minutes_sum)].index.tolist())
    return incomplete


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate",    required=True)
    ap.add_argument("--out",      required=True)
    ap.add_argument("--date",     default="")
    ap.add_argument("--days",     type=int,   default=35)
    ap.add_argument("--cache",    default="wnba_espn_cache.csv")
    ap.add_argument("--db",       default="", help="Override DB path (default: data/cache/proporacle_ref.db)")
    ap.add_argument("--season",   default="2026")
    ap.add_argument(
        "--attach-stats-through",
        default="",
        help="YYYY-MM-DD: use ESPN cache rows through this date for stat_g*/last5 (e.g. end of 2025 finals). "
        "When set, --attach-stats-season and --attach-stats-lookback-days control the window; "
        "the scoreboard fetch loop still uses --date and --days.",
    )
    ap.add_argument(
        "--attach-stats-season",
        default="",
        help="Season label on cache rows when using --attach-stats-through (defaults to --season).",
    )
    ap.add_argument(
        "--attach-stats-lookback-days",
        type=int,
        default=240,
        help="Days before --attach-stats-through to include in the rolling window (default 240).",
    )
    ap.add_argument(
        "--no-include-prior-season-stats",
        action="store_true",
        help="Restrict rolling stats to a single SEASON label and the normal --days lookback only. "
        "Default: merge current and previous season cache rows and widen lookback so L5 can use prior-season games.",
    )
    ap.add_argument("--n",        type=int,   default=10)
    ap.add_argument(
        "--min-minutes-rolling",
        type=float,
        default=0.0,
        help="Only count games with at least this many minutes in stat_g*/L5/L10 (0=include all games).",
    )
    ap.add_argument("--sleep",    type=float, default=0.8)
    ap.add_argument("--retries",  type=int,   default=4)
    ap.add_argument("--timeout",  type=float, default=30.0)
    ap.add_argument("--debug-misses", default="wnba_no_espn_debug.csv")
    args = ap.parse_args()

    today = datetime.today()
    target_date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else today

    attach_through = str(args.attach_stats_through or "").strip()
    if attach_through:
        stat_target = datetime.strptime(attach_through, "%Y-%m-%d")
        stat_season = str(args.attach_stats_season or "").strip() or str(args.season)
        stat_days = int(args.attach_stats_lookback_days)
        print(
            f"→ Rolling stat window: SEASON={stat_season} "
            f"through {stat_target.date()} (lookback {stat_days}d); "
            f"fetch window still anchored to {target_date.date()}"
        )
    else:
        stat_target = target_date
        stat_season = str(args.season)
        stat_days = int(args.days)

    # Rolling-stat window: optional merge with prior WNBA season in cache (e.g. 2025 + 2026) + longer lookback
    include_prior_for_stats = (not attach_through) and (not bool(getattr(args, "no_include_prior_season_stats", False)))
    merged_season_labels: set[str] = {str(stat_season)}
    effective_stat_days = int(stat_days)
    if include_prior_for_stats:
        sy = str(stat_season).strip()[:4]
        try:
            y = int(sy)
            if 2000 < y < 2100:
                merged_season_labels.add(str(y - 1))
        except ValueError:
            pass
        effective_stat_days = max(effective_stat_days, 420)
        print(
            f"→ Prior-season merge for rolling stats: SEASON in {sorted(merged_season_labels)} "
            f"(lookback {effective_stat_days}d, end {pd.Timestamp(stat_target).date()})"
        )

    print(f"→ Loading slate: {args.slate}")
    slate = pd.read_csv(args.slate, dtype=str, encoding="utf-8-sig").fillna("")
    prop_vals = sorted({str(v).strip().lower() for v in slate.get("prop_norm", slate.get("prop_type", "")).astype(str).tolist() if str(v).strip()})

    # Central DB (WNBA boxscores) — always attempt to keep it updated
    db_path = Path(args.db) if args.db else None
    con = open_db(db_path)
    ensure_wnba_schema(con)

    # ── Load / update ESPN cache ──────────────────────────────────────────────
    cache_path = Path(args.cache)
    if cache_path.exists():
        print(f"→ Loading cache: {cache_path}")
        cache = pd.read_csv(cache_path, dtype=str, encoding="utf-8-sig").fillna("")
    else:
        cache = pd.DataFrame()

    # Determine date range to fetch
    fetch_dates: List[datetime] = []
    for i in range(args.days):
        d = target_date - timedelta(days=i)
        if d > today:
            continue
        if _is_allstar(d):
            continue
        fetch_dates.append(d)

    existing_events: set = set()
    incomplete_events: set = set()
    if not cache.empty and "event_id" in cache.columns:
        existing_events = set(cache["event_id"].astype(str).unique())
        incomplete_events = find_incomplete_wnba_events(cache)
        if incomplete_events:
            print(
                f"→ Re-fetching {len(incomplete_events)} incomplete/partial event(s) in cache "
                f"(examples: {sorted(incomplete_events)[:5]})"
            )

    new_rows: List[dict] = []
    events_fetched = events_skipped = 0

    for d in fetch_dates:
        yyyymmdd = d.strftime("%Y%m%d")
        try:
            event_ids = fetch_event_ids(yyyymmdd, args.timeout, args.retries, args.sleep)
        except Exception as e:
            print(f"  [WARN] Scoreboard fetch failed for {yyyymmdd}: {e}")
            continue

        for eid in event_ids:
            if eid in existing_events and eid not in incomplete_events:
                continue
            try:
                url     = SUMMARY_URL.format(event_id=eid)
                summary = espn_get(url, args.timeout, args.retries, args.sleep)
                df_box  = parse_boxscore(summary, scoreboard_date=d.strftime("%Y-%m-%d"))
                if df_box.empty:
                    events_skipped += 1
                    continue
                df_box["event_id"] = eid
                df_box["SEASON"]   = args.season
                new_rows.extend(df_box.to_dict("records"))
                # Write into central SQLite DB (one row per player per event)
                # Normalize to build_boxscore_ref-style column names.
                rows_db = []
                for r in df_box.to_dict("records"):
                    rows_db.append({
                        "game_date": str(r.get("game_date", ""))[:10],
                        "event_id": str(r.get("event_id", "")),
                        "league": "WNBA",
                        "home_team": None,
                        "away_team": None,
                        "player": str(r.get("PLAYER_NAME", "")).strip(),
                        "team": str(r.get("TEAM", "")).strip().upper() or None,
                        "position": None,
                        "espn_athlete_id": str(r.get("ESPN_ATHLETE_ID", "")).strip() or None,
                        "minutes": _parse_minutes(r.get("MIN")) if isinstance(r.get("MIN"), str) else (r.get("MIN") if r.get("MIN") is not None else None),
                        "pts": float(r["PTS"]) if r.get("PTS") not in (None, "") and not (isinstance(r.get("PTS"), float) and np.isnan(r.get("PTS"))) else None,
                        "reb": float(r["REB"]) if r.get("REB") not in (None, "") and not (isinstance(r.get("REB"), float) and np.isnan(r.get("REB"))) else None,
                        "ast": float(r["AST"]) if r.get("AST") not in (None, "") and not (isinstance(r.get("AST"), float) and np.isnan(r.get("AST"))) else None,
                        "stl": float(r["STL"]) if r.get("STL") not in (None, "") and not (isinstance(r.get("STL"), float) and np.isnan(r.get("STL"))) else None,
                        "blk": float(r["BLK"]) if r.get("BLK") not in (None, "") and not (isinstance(r.get("BLK"), float) and np.isnan(r.get("BLK"))) else None,
                        "tov": float(r["TO"]) if r.get("TO") not in (None, "") and not (isinstance(r.get("TO"), float) and np.isnan(r.get("TO"))) else None,
                        "fgm": float(r["FGM"]) if r.get("FGM") not in (None, "") and not (isinstance(r.get("FGM"), float) and np.isnan(r.get("FGM"))) else None,
                        "fga": float(r["FGA"]) if r.get("FGA") not in (None, "") and not (isinstance(r.get("FGA"), float) and np.isnan(r.get("FGA"))) else None,
                        "fg3m": float(r["FG3M"]) if r.get("FG3M") not in (None, "") and not (isinstance(r.get("FG3M"), float) and np.isnan(r.get("FG3M"))) else None,
                        "fg3a": float(r["FG3A"]) if r.get("FG3A") not in (None, "") and not (isinstance(r.get("FG3A"), float) and np.isnan(r.get("FG3A"))) else None,
                        "fg2m": float(r["FG2M"]) if r.get("FG2M") not in (None, "") and not (isinstance(r.get("FG2M"), float) and np.isnan(r.get("FG2M"))) else None,
                        "fg2a": float(r["FG2A"]) if r.get("FG2A") not in (None, "") and not (isinstance(r.get("FG2A"), float) and np.isnan(r.get("FG2A"))) else None,
                        "ftm": float(r["FTM"]) if r.get("FTM") not in (None, "") and not (isinstance(r.get("FTM"), float) and np.isnan(r.get("FTM"))) else None,
                        "fta": float(r["FTA"]) if r.get("FTA") not in (None, "") and not (isinstance(r.get("FTA"), float) and np.isnan(r.get("FTA"))) else None,
                        "oreb": None,
                        "dreb": None,
                        "pf": None,
                        "pra": None,
                        "pr": None,
                        "pa": None,
                        "ra": None,
                        "bs": None,
                        "fantasy_score": None,
                    })
                upsert_rows(con, "wnba", rows_db)
                existing_events.add(eid)
                events_fetched += 1
            except Exception as e:
                print(f"  [WARN] Event {eid} failed: {e}")
                log_pipeline_health(
                    "wnba.step4_fetch_player_stats",
                    f"event_failed: {eid}",
                    extra={"event_id": eid, "error": f"{type(e).__name__}: {e}"},
                    start=Path(__file__),
                )
                events_skipped += 1

    print(f"ESPN fetch: {events_fetched} new events, {events_skipped} skipped")

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        refreshed_eids = set(new_df["event_id"].astype(str).unique())
        if refreshed_eids and not cache.empty:
            cache = cache[~cache["event_id"].astype(str).isin(refreshed_eids)].copy()
        cache  = pd.concat([cache, new_df], ignore_index=True) if not cache.empty else new_df
        cache.to_csv(cache_path, index=False, encoding="utf-8-sig")
        print(f"Cache updated → {cache_path}  ({len(cache)} rows)")

    if cache.empty:
        print("⚠️ Cache empty — writing slate with no stats attached")
        slate.to_csv(args.out, index=False, encoding="utf-8-sig")
        copy_pipeline_output_to_dated_dirs(
            output_path=args.out,
            df=slate,
            sport_dir_name="WNBA",
            repo_root=_PROPORACLE_ROOT,
        )
        return

    # Filter cache to season + date range (for props / stat_g*), independent of fetch window when attach-* is set
    cache_dates = pd.to_datetime(cache["game_date"], errors="coerce")
    cutoff_stat = stat_target - timedelta(days=int(effective_stat_days))
    if "SEASON" not in cache.columns:
        if attach_through:
            raise RuntimeError(
                "wnba_espn_cache.csv has no SEASON column. Run scripts/backfill_wnba_espn_range.py "
                "for 2025 (or re-fetch with step4) before using --attach-stats-through."
            )
        season_mask = pd.Series(True, index=cache.index)
    else:
        season_mask = cache["SEASON"].fillna("").astype(str).isin(merged_season_labels)
    cache_filt = cache[
        season_mask
        & (cache_dates >= pd.Timestamp(cutoff_stat))
        & (cache_dates <= pd.Timestamp(stat_target))
    ].copy()
    cache_filt = cache_filt.sort_values("game_date", ascending=False)

    # Build name→id map
    if not cache_filt.empty and "PLAYER_NORM" in cache_filt.columns:
        name_to_id: Dict[str, str] = (
            cache_filt.drop_duplicates("PLAYER_NORM")
            .set_index("PLAYER_NORM")["ESPN_ATHLETE_ID"].to_dict()
        )
    else:
        name_to_id = {}

    # ── Attach stats to slate ─────────────────────────────────────────────────
    N = args.n
    new_cols: Dict[str, List] = {
        **{f"stat_g{i}": [] for i in range(1, N+1)},
        "stat_last5_avg": [], "stat_last10_avg": [], "stat_season_avg": [],
        "last5_over": [], "last5_under": [], "last5_push": [], "last5_hit_rate": [],
        "line_hit_rate_over_ou_5":  [], "line_hit_rate_under_ou_5":  [],
        "line_hit_rate_over_ou_10": [], "line_hit_rate_under_ou_10": [],
        "unsupported_prop": [], "unsupported_reason": [],
        "espn_athlete_id": [],
    }

    misses = []

    for _, row in slate.iterrows():
        player   = str(row.get("player","")).strip()
        prop_n   = resolve_prop_slug(row)
        line_val = pd.to_numeric(row.get("line",""), errors="coerce")

        if _is_combo_row(row):
            p1 = str(row.get("player_1", "")).strip() or split_combo_name(player)[0]
            p2 = str(row.get("player_2", "")).strip() or split_combo_name(player)[1]
            if not p1 or not p2:
                misses.append({"player": player, "reason": "COMBO_SPLIT_FAILED"})
                _append_empty_stat_row(new_cols)
                continue
            e1 = _lookup_espn_id(p1, name_to_id)
            e2 = _lookup_espn_id(p2, name_to_id)
            if not e1 or not e2:
                misses.append({"player": player, "reason": "NO_ESPN_ID_COMBO"})
                _append_empty_stat_row(new_cols)
                continue
            vals_mr, game_frames = _combo_stat_values(
                cache_filt, [e1, e2], prop_n, N, float(args.min_minutes_rolling)
            )
            if not vals_mr:
                misses.append({"player": player, "reason": "NO_CACHE_GAMES_COMBO"})
                _append_empty_stat_row(new_cols)
                new_cols["espn_athlete_id"][-1] = f"{e1}|{e2}"
                continue
            if all(isinstance(v, float) and np.isnan(v) for v in vals_mr):
                misses.append({"player": player, "reason": f"UNSUPPORTED_PROP:{prop_n}"})
                for k in new_cols:
                    new_cols[k].append(np.nan)
                new_cols["unsupported_prop"][-1] = 1
                new_cols["unsupported_reason"][-1] = f"UNSUPPORTED_PROP:{prop_n}"
                new_cols["espn_athlete_id"][-1] = f"{e1}|{e2}"
                continue
            ref_games = game_frames[0] if game_frames else pd.DataFrame()
            _append_stat_row(new_cols, vals_mr, line_val, N, f"{e1}|{e2}", ref_games)
            continue

        p_norm   = _norm_name(player)
        ath_id = name_to_id.get(p_norm, "")

        if not ath_id:
            misses.append({"player": player, "reason": "NO_ESPN_ID"})
            _append_empty_stat_row(new_cols)
            continue

        vals_mr, player_games = _stat_values_for_athlete(
            cache_filt, ath_id, prop_n, N, float(args.min_minutes_rolling)
        )

        if not vals_mr:
            misses.append({"player": player, "reason": "NO_CACHE_GAMES"})
            for k in new_cols:
                new_cols[k].append(np.nan)
            new_cols["unsupported_prop"][-1]   = 0
            new_cols["unsupported_reason"][-1] = ""
            new_cols["espn_athlete_id"][-1]    = ath_id
            continue

        if all(isinstance(v, float) and np.isnan(v) for v in vals_mr):
            for k in new_cols:
                new_cols[k].append(np.nan)
            new_cols["unsupported_prop"][-1]   = 1
            new_cols["unsupported_reason"][-1] = f"UNSUPPORTED_PROP:{prop_n}"
            new_cols["espn_athlete_id"][-1]    = ath_id
            continue

        _append_stat_row(new_cols, vals_mr, line_val, N, ath_id, player_games)

    out = slate.copy()
    for k, v in new_cols.items():
        out[k] = v

    try:
        from role_stability import role_stability

        def _wnba_usage_l10(row: pd.Series) -> list:
            vals: list = []
            for i in range(1, 11):
                v = pd.to_numeric(row.get(f"stat_g{i}"), errors="coerce")
                if pd.notna(v) and float(v) >= 0:
                    vals.append(float(v))
            return vals

        out["minutes_L10_list"] = out.apply(_wnba_usage_l10, axis=1)
        out["role_stability_score"] = out["minutes_L10_list"].apply(role_stability)
        out["high_variance_role"] = pd.to_numeric(out["role_stability_score"], errors="coerce").lt(0.35)
    except Exception as _rs_exc:
        print(f"  [WARN] role_stability_score skipped: {_rs_exc}")

    wnba_b2b_ok = _wnba_team_keys_align(con, out)
    out = attach_b2b_columns(out, con, table="wnba", sport_label="WNBA", enabled=wnba_b2b_ok)

    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.out,
        df=out,
        sport_dir_name="WNBA",
        repo_root=_PROPORACLE_ROOT,
    )
    print(f"✅ Saved → {args.out}  rows={len(out)}")

    if misses and args.debug_misses:
        pd.DataFrame(misses).to_csv(args.debug_misses, index=False)
        print(f"  Debug misses → {args.debug_misses} ({len(misses)} rows)")

    cache_cols = list(cache_filt.columns) if not cache_filt.empty else list(cache.columns)
    print("  [diag] step1 prop_norm values:", prop_vals[:20], ("..." if len(prop_vals) > 20 else ""))
    print("  [diag] ESPN cache columns:", cache_cols[:25], ("..." if len(cache_cols) > 25 else ""))

    filled = int(pd.to_numeric(out.get("stat_last5_avg",""), errors="coerce").notna().sum())
    print(f"  stat_last5_avg filled: {filled}/{len(out)}")
    con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_pipeline_health(
            "wnba.step4_fetch_player_stats",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        # Avoid crashing the whole run; exit gracefully.
        print(f"❌ WNBA step4 failed (logged). {type(e).__name__}: {e}")
