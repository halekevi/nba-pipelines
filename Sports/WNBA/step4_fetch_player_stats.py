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
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[1]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import ensure_wnba_schema, log_pipeline_health, open_db, upsert_rows

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


def parse_boxscore(summary: dict) -> pd.DataFrame:
    """Parse full-game WNBA boxscore → one row per player per game."""
    box   = (summary or {}).get("boxscore") or {}
    rows  = []

    game_date = ""
    header    = (summary or {}).get("header") or {}
    comp      = header.get("competitions") or []
    if comp:
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

            stat_map: Dict[str, str] = {}
            for k, v in zip(keys, raw_stats):
                stat_map[k] = str(v)

            def _g(k: str) -> str:
                return stat_map.get(k, stat_map.get(k.lower(), ""))

            # Parse shooting: ESPN returns "FGM-FGA", "3PM-3PA", "FTM-FTA"
            fgm, fga   = _parse_made_att(_g("FG") or _g("FGM-FGA"))
            fg3m, fg3a = _parse_made_att(_g("3PT") or _g("3PM-3PA"))
            ftm, fta   = _parse_made_att(_g("FT") or _g("FTM-FTA"))
            fg2m = (fgm - fg3m) if not (np.isnan(fgm) or np.isnan(fg3m)) else np.nan
            fg2a = (fga - fg3a) if not (np.isnan(fga) or np.isnan(fg3a)) else np.nan

            row_out = {
                "game_date":        game_date,
                "event_id":         event_id,
                "ESPN_ATHLETE_ID":  ath_id,
                "PLAYER_NAME":      ath_name,
                "PLAYER_NORM":      ath_norm,
                "TEAM":             team_abbr,
                "MIN":              _parse_minutes(_g("MIN")),
                "PTS":              pd.to_numeric(_g("PTS"), errors="coerce"),
                "REB":              pd.to_numeric(_g("REB") or _g("DREB"), errors="coerce"),
                "AST":              pd.to_numeric(_g("AST"), errors="coerce"),
                "STL":              pd.to_numeric(_g("STL"), errors="coerce"),
                "BLK":              pd.to_numeric(_g("BLK"), errors="coerce"),
                "TO":               pd.to_numeric(_g("TO") or _g("TOV"), errors="coerce"),
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
    if p == "fga":                      return fga
    if p == "fgm":                      return fgm
    if p in ("fg3a","3pta"):            return fg3a
    if p in ("fg3m","3ptm"):            return fg3m
    if p in ("fg2a","2pta"):            return fg2a
    if p in ("fg2m","2ptm"):            return fg2m
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
    ap.add_argument("--n",        type=int,   default=10)
    ap.add_argument("--sleep",    type=float, default=0.8)
    ap.add_argument("--retries",  type=int,   default=4)
    ap.add_argument("--timeout",  type=float, default=30.0)
    ap.add_argument("--debug-misses", default="wnba_no_espn_debug.csv")
    args = ap.parse_args()

    today = datetime.today()
    target_date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else today

    print(f"→ Loading slate: {args.slate}")
    slate = pd.read_csv(args.slate, dtype=str, encoding="utf-8-sig").fillna("")

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
    if not cache.empty and "event_id" in cache.columns:
        existing_events = set(cache["event_id"].astype(str).unique())

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
            if eid in existing_events:
                continue
            try:
                url     = SUMMARY_URL.format(event_id=eid)
                summary = espn_get(url, args.timeout, args.retries, args.sleep)
                df_box  = parse_boxscore(summary)
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
        cache  = pd.concat([cache, new_df], ignore_index=True) if not cache.empty else new_df
        cache.to_csv(cache_path, index=False, encoding="utf-8-sig")
        print(f"Cache updated → {cache_path}  ({len(cache)} rows)")

    if cache.empty:
        print("⚠️ Cache empty — writing slate with no stats attached")
        slate.to_csv(args.out, index=False, encoding="utf-8-sig")
        return

    # Filter cache to season + date range
    cache_dates = pd.to_datetime(cache["game_date"], errors="coerce")
    cutoff = target_date - timedelta(days=args.days)
    cache_filt = cache[
        (cache.get("SEASON","") == args.season) &
        (cache_dates >= pd.Timestamp(cutoff)) &
        (cache_dates <= pd.Timestamp(target_date))
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
        prop_n   = str(row.get("prop_norm", row.get("prop_type",""))).lower().strip()
        line_val = pd.to_numeric(row.get("line",""), errors="coerce")
        p_norm   = _norm_name(player)

        # Resolve ESPN athlete ID
        ath_id = name_to_id.get(p_norm, "")

        if not ath_id:
            misses.append({"player": player, "reason": "NO_ESPN_ID"})
            for k in new_cols: new_cols[k].append(np.nan if "rate" in k or "avg" in k else ("" if k == "unsupported_reason" else np.nan))
            new_cols["unsupported_prop"][-1]   = 0
            new_cols["unsupported_reason"][-1] = ""
            new_cols["espn_athlete_id"][-1]    = ""
            # overwrite rate/avg with nan already done; just fix string cols
            continue

        player_games = cache_filt[cache_filt["ESPN_ATHLETE_ID"].astype(str) == str(ath_id)].copy()

        if player_games.empty:
            misses.append({"player": player, "reason": "NO_CACHE_GAMES"})
            for k in new_cols: new_cols[k].append(np.nan)
            new_cols["unsupported_prop"][-1]   = 0
            new_cols["unsupported_reason"][-1] = ""
            new_cols["espn_athlete_id"][-1]    = ath_id
            continue

        stat_series = derive_stat(player_games, prop_n)

        if stat_series.isna().all():
            for k in new_cols: new_cols[k].append(np.nan)
            new_cols["unsupported_prop"][-1]   = 1
            new_cols["unsupported_reason"][-1] = f"UNSUPPORTED_PROP:{prop_n}"
            new_cols["espn_athlete_id"][-1]    = ath_id
            continue

        vals_mr = [float(v) if not pd.isna(v) else np.nan for v in stat_series.tolist()][:N]

        for i in range(N):
            new_cols[f"stat_g{i+1}"].append(vals_mr[i] if i < len(vals_mr) else np.nan)

        valid_vals = [v for v in vals_mr if not (isinstance(v, float) and np.isnan(v))]
        new_cols["stat_last5_avg"].append(float(np.mean(valid_vals[:5])) if valid_vals[:5] else np.nan)
        new_cols["stat_last10_avg"].append(float(np.mean(valid_vals[:10])) if valid_vals[:10] else np.nan)

        season_col = "stat_season_avg"
        if "SEASON_AVG" in player_games.columns:
            sv = pd.to_numeric(player_games["SEASON_AVG"], errors="coerce").dropna()
            new_cols[season_col].append(float(sv.mean()) if len(sv) else np.nan)
        else:
            new_cols[season_col].append(float(np.mean(valid_vals)) if valid_vals else np.nan)

        if not np.isnan(line_val):
            o5, u5, p5, hr5, hr5_ou, ur5_ou = calc_hit_context(vals_mr, line_val, 5)
            o10, u10, p10, hr10, hr10_ou, ur10_ou = calc_hit_context(vals_mr, line_val, 10)
            new_cols["last5_over"].append(o5)
            new_cols["last5_under"].append(u5)
            new_cols["last5_push"].append(p5)
            new_cols["last5_hit_rate"].append(hr5)
            new_cols["line_hit_rate_over_ou_5"].append(hr5_ou)
            new_cols["line_hit_rate_under_ou_5"].append(ur5_ou)
            new_cols["line_hit_rate_over_ou_10"].append(hr10_ou)
            new_cols["line_hit_rate_under_ou_10"].append(ur10_ou)
        else:
            for k in ["last5_over","last5_under","last5_push","last5_hit_rate",
                      "line_hit_rate_over_ou_5","line_hit_rate_under_ou_5",
                      "line_hit_rate_over_ou_10","line_hit_rate_under_ou_10"]:
                new_cols[k].append(np.nan)

        new_cols["unsupported_prop"].append(0)
        new_cols["unsupported_reason"].append("")
        new_cols["espn_athlete_id"].append(ath_id)

    out = slate.copy()
    for k, v in new_cols.items():
        out[k] = v

    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.out}  rows={len(out)}")

    if misses and args.debug_misses:
        pd.DataFrame(misses).to_csv(args.debug_misses, index=False)
        print(f"  Debug misses → {args.debug_misses} ({len(misses)} rows)")

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
