#!/usr/bin/env python3
"""
cbb_step5b_attach_boxscore_stats.py  (upgraded)
------------------------------------------------
Mirrors NBA step4 logic exactly.

Improvements over original:
- stat_season_avg added (all games in window)
- stat_last10_avg already present, now also stat_last5_avg
- line_hit_rate_over_ou_5  (last 5 vs line, excl push)
- line_hit_rate_over_ou_10 (last 10 vs line, excl push)  ← NEW
- line_hit_rate_over_5 / line_hit_rate_under_5
- MIN averages: min_last5_avg, min_season_avg
- Matches by espn_athlete_id first, then player_norm fallback

Input : step2_normalized_cbb.csv  (or step5_with_espn_ids.csv)
Output: step5b_with_stats_cbb.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import pandas as pd
import requests

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[3]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import log_pipeline_health

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/scoreboard"
ESPN_SUMMARY_URL    = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/{league}/summary"
ESPN_LEAGUE = "mens-college-basketball"


def norm(s: str) -> str:
    """Canonical player name normalizer — matches cbb_step2_normalize.norm_str()."""
    s = (s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def request_json(url, params=None, max_tries=5, backoff=1.4, sleep=0.0):
    for i in range(1, max_tries + 1):
        try:
            if sleep: time.sleep(sleep)
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff ** (i - 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(backoff ** (i - 1))
    log_pipeline_health(
        "cbb.step5b_attach_boxscore_stats",
        "request_json_failed",
        extra={"url": url, "params": params, "max_tries": max_tries},
        start=Path(__file__),
    )
    return None


def date_range(end_date: dt.date, days_back: int) -> List[str]:
    return [(end_date - dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(days_back + 1)]


def pull_scoreboard(d: str) -> dict:
    return request_json(ESPN_SCOREBOARD_URL.format(league=ESPN_LEAGUE),
                        params={"dates": d, "groups": "50", "limit": "500"},
                        sleep=0.10) or {}


def extract_events(sb: dict) -> List[Tuple[str, str, str, str]]:
    """Return list of (eid, team1_id, team2_id, date_str YYYYMMDD)."""
    out = []
    for ev in sb.get("events", []) or []:
        eid = str(ev.get("id", "")).strip()
        if not eid:
            continue
        date_str = str(ev.get("date", ""))[:10].replace("-", "")
        comps = ev.get("competitions", []) or []
        if not comps:
            continue
        competitors = comps[0].get("competitors", []) or []
        tids = []
        for c in competitors:
            tid = str((c.get("team") or {}).get("id", "")).strip()
            if tid:
                tids.append(tid)
        if len(tids) >= 2:
            out.append((eid, tids[0], tids[1], date_str))
    return out


def pull_summary(eid: str) -> dict:
    return request_json(ESPN_SUMMARY_URL.format(league=ESPN_LEAGUE), params={"event": eid}, sleep=0.08) or {}


def parse_min(x) -> float:
    try:
        s = str(x).strip()
        if s in ("", "--", "nan", "None"): return 0.0
        if ":" in s:
            mm, ss = s.split(":", 1)
            return float(mm) + float(ss) / 60.0
        return float(s)
    except Exception:
        return 0.0


def parse_players(summary: dict, game_date: str = "", event_id: str = "") -> List[dict]:
    """Parse player boxscore rows; attaches game_date and event_id for sorting/dedup."""
    if not game_date:
        hdr = summary.get("header", {}) or {}
        comps = hdr.get("competitions", [{}])
        game_date = str(comps[0].get("date", "") if comps else "")[:10].replace("-", "")

    box    = summary.get("boxscore", {}) or {}
    blocks = box.get("players", []) or []
    rows   = []

    # Collect all team_ids in this game first so we can derive opponent per block
    all_team_ids = [
        str((tb.get("team") or {}).get("id", "")).strip()
        for tb in blocks
        if str((tb.get("team") or {}).get("id", "")).strip()
    ]

    for tb in blocks:
        team_id = str((tb.get("team") or {}).get("id", "")).strip()
        if not team_id: continue

        # Opponent = the other team in this game
        opp_team_id = next((t for t in all_team_ids if t != team_id), "")

        labels = athletes = None
        for g in tb.get("statistics", []) or []:
            if isinstance(g, dict) and "labels" in g and "athletes" in g:
                labels   = g.get("labels", [])
                athletes = g.get("athletes", [])
                if any(str(x).upper() == "PTS" for x in labels): break

        if not labels or not athletes: continue
        ul = [str(x).upper() for x in labels]

        def idx(lbl):
            return ul.index(lbl) if lbl in ul else None

        i = {k: idx(k) for k in ("MIN","PTS","REB","AST","STL","BLK")}
        i["TO"]  = idx("TO") if "TO" in ul else idx("TOV")
        i["3PM"] = next((idx(lbl) for lbl in ("3PM","FG3M","3PT","3PTM","3FGM","3-PT") if lbl in ul), None)

        for a in athletes:
            ath = a.get("athlete", {}) or {}
            aid = str(ath.get("id", "")).strip()
            pn  = norm(ath.get("displayName") or ath.get("fullName") or "")
            st  = a.get("stats", []) or []

            def getf(key):
                ii = i.get(key)
                if ii is None or ii >= len(st): return 0.0
                val = st[ii]
                try:
                    # ESPN shooting stats use "made-attempted" slash notation (e.g. "3-8")
                    # Take the left side (made) for all slash stats
                    if isinstance(val, str) and "-" in val:
                        val = val.split("-")[0]
                    return float(val)
                except Exception: return 0.0

            min_val = parse_min(st[i["MIN"]]) if i["MIN"] is not None and i["MIN"] < len(st) else 0.0

            if pn:
                row_out = {"team_id": team_id, "opp_team_id": opp_team_id,
                           "player_norm": pn,
                           "espn_athlete_id": aid,
                           "game_date": game_date,
                           "event_id":  event_id,
                           "MIN": min_val,
                           "PTS": getf("PTS"), "REB": getf("REB"),
                           "AST": getf("AST"), "STL": getf("STL"),
                           "BLK": getf("BLK"), "TO":  getf("TO"),
                           "3PM": getf("3PM")}

                # ── Bouncer: reject impossible/junk player rows ─────────────
                def _neg(x) -> bool:
                    try:
                        return float(x) < 0
                    except Exception:
                        return True

                if any(_neg(row_out.get(k, 0.0)) for k in ("MIN","PTS","REB","AST","STL","BLK","TO","3PM")):
                    continue
                # generous plausibility caps
                if row_out["MIN"] > 60 or row_out["PTS"] > 120 or row_out["REB"] > 60 or row_out["AST"] > 40:
                    continue
                if row_out["STL"] > 20 or row_out["BLK"] > 20 or row_out["TO"] > 30 or row_out["3PM"] > 30:
                    continue

                rows.append(row_out)
    return rows


def fantasy(r: dict) -> float:
    return (r.get("PTS",0) + 1.2*r.get("REB",0) + 1.5*r.get("AST",0)
            + 3*r.get("STL",0) + 3*r.get("BLK",0) - r.get("TO",0))


def prop_value(prop_norm: str, r: dict) -> Optional[float]:
    p = str(prop_norm or "").strip().lower()
    three = r.get("3PM")
    m = {"pts": r.get("PTS"), "reb": r.get("REB"), "ast": r.get("AST"),
         "stl": r.get("STL"), "blk": r.get("BLK"), "tov": r.get("TO"),
         "3pm": three, "fg3m": three,  # fg3m alias matches step6 prop_norm
         "stocks": r.get("STL",0)+r.get("BLK",0),
         "pra": r.get("PTS",0)+r.get("REB",0)+r.get("AST",0),
         "pr":  r.get("PTS",0)+r.get("REB",0),
         "pa":  r.get("PTS",0)+r.get("AST",0),
         "ra":  r.get("REB",0)+r.get("AST",0),
         "fantasy": fantasy(r)}
    # also handle full text fallbacks
    if "fantasy" in p: return fantasy(r)
    if "points"  in p: return r.get("PTS")
    if "rebounds" in p: return r.get("REB")
    if "assists"  in p: return r.get("AST")
    if "steals"   in p: return r.get("STL")
    if "blocks"   in p: return r.get("BLK")
    if "turnovers" in p or p in ("to","tov"): return r.get("TO")
    if "3-pt" in p or "3pt" in p or "3pm" in p or "fg3m" in p or "threes" in p: return three
    return m.get(p)


def hit_rates(vals: List[float], line: float, n: int):
    """Compute hit rate over/under/push for last n games, excl push."""
    sub = vals[:n]
    over = sum(1 for v in sub if v > line)
    under = sum(1 for v in sub if v < line)
    push  = sum(1 for v in sub if v == line)
    denom_ou = len(sub) - push
    hr_over_ou  = over  / denom_ou if denom_ou > 0 else None
    hr_under_ou = under / denom_ou if denom_ou > 0 else None
    hr_over     = over  / len(sub) if sub else None
    hr_under    = under / len(sub) if sub else None
    return over, under, push, hr_over, hr_under, hr_over_ou, hr_under_ou


def _fetch_one_event_cbb(
    eid: str,
    t1: str,
    t2: str,
    slate_ids: set,
    date_str: str = "",   # FIX: was missing, causing TypeError
) -> Tuple[str, List[dict]]:
    """Fetch and parse a single CBB ESPN event. Returns (eid, player_rows)."""
    if slate_ids and t1 not in slate_ids and t2 not in slate_ids:
        return eid, []
    try:
        time.sleep(random.uniform(0.05, 0.25))
        summ = pull_summary(eid)
        return eid, parse_players(summ, game_date=date_str, event_id=eid)
    except Exception as e:
        print(f"  [WARN] CBB summary failed event={eid}: {e}")
        log_pipeline_health(
            "cbb.step5b_attach_boxscore_stats",
            "event_summary_failed",
            extra={"event_id": eid, "error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        return eid, []


def build_player_histories(
    days: int,
    slate_ids: set,
    workers: int = 4,
    cache_path: str = "",
    tid_to_abbr: dict = None,
) -> Tuple[Dict, Dict]:
    """
    Parallelized boxscore fetch for CBB with persistent cache + deterministic ordering.
    Returns (hist_aid, hist_name) sorted newest-first per player.
    """
    import os

    # Phase 0: load cache
    cached_rows: List[dict] = []
    cached_eids: set = set()
    if cache_path and os.path.exists(cache_path):
        try:
            cache_df = pd.read_csv(cache_path, dtype=str).fillna("")
            cached_rows = cache_df.to_dict("records")
            stale_eids: set = set()
            for rr in cached_rows:
                has_opp = (
                    ("opp_team_abbr" in rr and str(rr.get("opp_team_abbr","")).strip() not in ("", "nan"))
                    or ("opp_team_id" in rr and str(rr.get("opp_team_id","")).strip() not in ("", "nan"))
                )
                for col in ("MIN","PTS","REB","AST","STL","BLK","TO","3PM"):
                    try: rr[col] = float(rr[col])
                    except Exception: rr[col] = 0.0
                if not has_opp:
                    stale_eids.add(str(rr.get("event_id","")))
            # Remove stale rows so they get refetched fresh
            if stale_eids:
                cached_rows = [rr for rr in cached_rows if str(rr.get("event_id","")) not in stale_eids]
                print(f"  [CACHE] Dropped {len(stale_eids)} stale events missing 3PM — will refetch")
            cached_eids = {str(rr.get("event_id","")) for rr in cached_rows if rr.get("event_id")}
            print(f"  [CACHE] Loaded {len(cached_rows)} rows ({len(cached_eids)} events)")
        except Exception as e:
            print(f"  [CACHE] Load failed ({e}) — full refresh")
            cached_rows, cached_eids = [], set()
    # Phase 1: scoreboards
    all_events: List[Tuple[str, str, str, str]] = []
    seen_eids: set = set()
    print(f"-> Scanning {days + 1} days of CBB scoreboards...")
    try:
        from tqdm import tqdm as _tqdm
    except ImportError:
        import subprocess as _sp, sys as _sys
        _sp.check_call([_sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
        from tqdm import tqdm as _tqdm
    for d in _tqdm(date_range(dt.date.today(), days), desc="Scanning scoreboards", unit="day"):
        sb = pull_scoreboard(d)
        for eid, t1, t2, date_str in extract_events(sb):
            if eid not in seen_eids:
                seen_eids.add(eid)
                all_events.append((eid, t1, t2, date_str))

    if slate_ids:
        all_events = [(e, t1, t2, ds) for e, t1, t2, ds in all_events
                      if t1 in slate_ids or t2 in slate_ids]

    pending = [(eid, t1, t2, ds) for eid, t1, t2, ds in all_events
               if eid not in cached_eids]

    print(f"-> {len(all_events)} total | {len(cached_eids)} cached | "
          f"{len(pending)} new ({workers} workers)...")

    # Phase 2: parallel fetch
    new_rows: List[dict] = []
    if pending:
        from concurrent.futures import ThreadPoolExecutor, as_completed as _ac
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_one_event_cbb, eid, t1, t2, slate_ids, ds): eid
                for eid, t1, t2, ds in pending
            }
            fetched = 0
            with _tqdm(total=len(pending), desc="Fetching games", unit="game") as pbar:
                for future in _ac(futures):
                    eid, rows = future.result()
                    if rows:
                        fetched += 1
                        new_rows.extend(rows)
                    pbar.update(1)
        print(f"  [FETCH] Got {fetched} new games with player data")

    # Phase 3: update cache
    all_rows = cached_rows + new_rows
    # Resolve opp_team_id -> opp_team_abbr using slate map (for H2H matching in step6)
    if tid_to_abbr:
        for rr in all_rows:
            if not rr.get("opp_team_abbr"):
                opp_id = str(rr.get("opp_team_id", "")).strip()
                rr["opp_team_abbr"] = tid_to_abbr.get(opp_id, opp_id)
    if cache_path and new_rows:
        try:
            pd.DataFrame(all_rows).to_csv(cache_path, index=False)
            print(f"  [CACHE] Saved {len(all_rows)} rows -> {cache_path}")
        except Exception as e:
            print(f"  [CACHE] Save failed: {e}")

    # Phase 4: build sorted, deduplicated histories (newest-first)
    raw_by_aid:  Dict[Tuple[str,str], List[dict]] = {}
    raw_by_name: Dict[Tuple[str,str], List[dict]] = {}
    for rr in all_rows:
        tid = str(rr.get("team_id","")).strip()
        aid = str(rr.get("espn_athlete_id","")).strip()
        pn  = str(rr.get("player_norm","")).strip()
        if tid and aid: raw_by_aid.setdefault((tid, aid), []).append(rr)
        if tid and pn:  raw_by_name.setdefault((tid, pn), []).append(rr)

    def dedup_sort(game_list):
        sorted_games = sorted(game_list, key=lambda r: r.get("game_date",""), reverse=True)
        seen, out = set(), []
        for g in sorted_games:
            eid = str(g.get("event_id",""))
            if eid and eid in seen: continue
            if eid: seen.add(eid)
            out.append(g)
        return out

    hist_aid  = {k: dedup_sort(v) for k, v in raw_by_aid.items()}
    hist_name = {k: dedup_sort(v) for k, v in raw_by_name.items()}

    print(f"-> CBB histories built | by_id={len(hist_aid)} | by_name={len(hist_name)}")
    return hist_aid, hist_name


def main():
    try:
        from tqdm import tqdm as _tqdm
    except ImportError:
        import subprocess as _sp, sys as _sys
        _sp.check_call([_sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
        from tqdm import tqdm as _tqdm

    ap = argparse.ArgumentParser()
    ap.add_argument("--input",    required=True)
    ap.add_argument("--output",   default="step5b_with_stats_cbb.csv")
    ap.add_argument("--days",     type=int, default=90,
                    help="Days of history to scan (default 90; use 120 for full season)")
    ap.add_argument("--n",        type=int, default=10)
    ap.add_argument("--workers",  type=int, default=4)
    ap.add_argument("--cache",    default="cbb_boxscore_cache.csv",
                    help="Persistent cache CSV path (default: cbb_boxscore_cache.csv)")
    ap.add_argument(
        "--league",
        default="auto",
        choices=["auto", "mens-college-basketball", "womens-college-basketball"],
        help="ESPN league slug for scoreboard/summary fetches (default: auto by wcbb in paths).",
    )
    ap.add_argument("--no_cache", action="store_true",
                    help="Ignore existing cache and force full refresh")
    args = ap.parse_args()
    cache_path = "" if args.no_cache else args.cache

    global ESPN_LEAGUE
    if args.league == "auto":
        hint = f"{args.input} {args.output} {cache_path}".lower()
        ESPN_LEAGUE = "womens-college-basketball" if "wcbb" in hint else "mens-college-basketball"
    else:
        ESPN_LEAGUE = args.league
    print(f"-> ESPN league: {ESPN_LEAGUE}")

    print("→ Loading:", args.input)
    try:
        df = pd.read_csv(args.input, dtype=str).fillna("")
    except Exception as e:
        log_pipeline_health(
            "cbb.step5b_attach_boxscore_stats",
            "read_failed",
            extra={"input": args.input, "error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        raise
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    if "player_norm" not in df.columns:
        df["player_norm"] = df["player"].astype(str).apply(norm)
    if "team_id" not in df.columns:
        df["team_id"] = ""
    if "espn_athlete_id" not in df.columns:
        df["espn_athlete_id"] = ""

    # ── Flag 2nd-half props before any stat attachment ────────────────────────
    # ESPN boxscores are full-game only. Props with duration="2nd Half" need a
    # separate data source — attaching full-game stats produces wrong averages
    # and hit rates (~2x the actual 2H numbers). Mark UNSUPPORTED_2H so step6
    # excludes them from scoring entirely rather than using corrupted stats.
    if "duration" in df.columns:
        h2_mask = df["duration"].str.lower().str.contains("2nd|half", na=False)
        n_h2 = int(h2_mask.sum())
        if n_h2:
            print(f"  ⚠️  {n_h2} 2nd-half props tagged UNSUPPORTED_2H — full-game ESPN stats invalid for 2H lines")
            df_2h = df[h2_mask].copy()
            df_2h["stat_status"] = "UNSUPPORTED_2H"
            df = df[~h2_mask].copy()
        else:
            df_2h = pd.DataFrame()
    else:
        df_2h = pd.DataFrame()

    # use prop_norm if available, else prop_type
    prop_col = "prop_norm" if "prop_norm" in df.columns else "prop_type"

    # ── Bouncer (slate-level) ───────────────────────────────────────────────
    before = len(df)
    df = df[df.get("player", "").astype(str).str.strip() != ""].copy()
    if "pp_team" in df.columns:
        df = df[df["pp_team"].astype(str).str.strip() != ""].copy()
    df = df[df[prop_col].astype(str).str.strip() != ""].copy()
    # Line must be numeric & non-negative for hit-rate math
    df = df[df["line"].notna() & (df["line"] >= 0)].copy()
    bounced = before - len(df)
    if bounced:
        print(f"  🧹 Bouncer: removed {bounced} junk slate rows")
        log_pipeline_health(
            "cbb.step5b_attach_boxscore_stats",
            "bouncer_removed_slate_rows",
            extra={"removed": bounced, "before": before, "after": len(df)},
            start=Path(__file__),
        )

    slate_ids = {x for x in df["team_id"].astype(str).str.strip() if x and x != "nan"}
    print("→ Slate team_ids:", len(slate_ids))

    # If NO_MATCH rows have blank team_id but we know the team from team_abbr,
    # fetch all games rather than filtering — the team_id filter is an optimization
    # but it silently drops players whose ESPN ID wasn't found. If >10% of rows
    # have no team_id, disable the slate_ids filter entirely for safety.
    no_team_id = (df["team_id"].astype(str).str.strip().isin(["", "nan"])).sum()
    if no_team_id > 0:
        print(f"→ {no_team_id} rows have no team_id — fetching all CBB games (no team filter)")
        slate_ids = set()  # empty set = fetch all games

    # Build team_id -> team_abbr reverse map from slate for opp resolution in cache
    tid_to_abbr: dict = {}
    for _, r in df.iterrows():
        tid  = str(r.get("team_id", "")).strip()
        abbr = str(r.get("team_abbr", "")).strip()
        if tid and abbr and tid != "nan":
            tid_to_abbr[tid] = abbr

    # ── Parallelized fetch ────────────────────────────────────────────────────
    hist_aid, hist_name = build_player_histories(
        args.days,
        slate_ids,
        workers=args.workers,
        cache_path=cache_path,
        tid_to_abbr=tid_to_abbr,
    )
    if not hist_aid and not hist_name:
        log_pipeline_health(
            "cbb.step5b_attach_boxscore_stats",
            "no_histories_built",
            extra={"days": args.days, "workers": args.workers},
            start=Path(__file__),
        )

    out_rows, stat_status = [], []

    for _, row in _tqdm(df.iterrows(), total=len(df), desc="Attaching stats", unit="prop"):
        tid  = str(row.get("team_id",       "")).strip()
        pn   = str(row.get("player_norm",   "")).strip()
        aid  = str(row.get("espn_athlete_id","")).strip()
        prop = str(row.get(prop_col,        "")).strip()
        line = row.get("line", None)

        # When team_id is missing, fall through to ID-only then name-only matching.
        # Handles CBB props where ESPN team_id was never resolved by step5a,
        # and also covers slates processed by attach_cbb_athlete_ids (espn_id present
        # but team_id blank).
        if not tid:
            games = []
            # Strategy A: espn_athlete_id across all teams (fastest, most accurate)
            if aid:
                for (t, a), g in hist_aid.items():
                    if a == aid:
                        games.extend(g)
            # Strategy B: player_norm across all teams (name-only fallback)
            if not games and pn:
                for (t, p), g in hist_name.items():
                    if p == pn:
                        games.extend(g)
            if not games:
                stat_status.append("NO_BOX_HISTORY"); out_rows.append({}); continue
        else:
            games = (hist_aid.get((tid, aid), []) if aid else []) or hist_name.get((tid, pn), [])
        if not games:
            stat_status.append("NO_BOX_HISTORY"); out_rows.append({}); continue

        played = [g for g in games if float(g.get("MIN", 0) or 0) > 0]
        vals = [float(v) for g in played
                if (v := prop_value(prop, g)) is not None]

        if not vals:
            stat_status.append("UNSUPPORTED_PROP"); out_rows.append({}); continue
        if len(vals) < 5:
            stat_status.append("INSUFFICIENT_GAMES"); out_rows.append({"games_used": len(vals)}); continue

        # vals is now newest-first (already sorted from dedup_sort)

        # FIX: preserve full season history BEFORE truncating to --n games
        season_vals = vals[:]           # full window
        game_log    = vals[:args.n]     # truncated for g1..gN columns
        last5       = game_log[:5]
        last10      = game_log[:10]

        o = {"games_used": len(season_vals)}   # true season game count
        for k in range(1, args.n + 1):
            o[f"stat_g{k}"] = game_log[k-1] if k-1 < len(game_log) else ""

        o["stat_last5_avg"]  = round(sum(last5)       / len(last5),       3) if last5       else ""
        o["stat_last10_avg"] = round(sum(last10)      / len(last10),      3) if last10      else ""
        o["stat_season_avg"] = round(sum(season_vals) / len(season_vals), 3) if season_vals else ""

        # minutes averages
        min_vals = [float(g.get("MIN", 0) or 0) for g in played]
        min5 = min_vals[:5]
        o["min_last5_avg"]   = round(sum(min5)    / len(min5),    1) if min5    else ""
        o["min_season_avg"]  = round(sum(min_vals) / len(min_vals), 1) if min_vals else ""

        # hit rates vs line
        if pd.notna(line):
            ln = float(line)

            over5, under5, push5, hr_ov5, hr_un5, hr_ov_ou5, hr_un_ou5 = hit_rates(game_log, ln, 5)
            o["line_hits_over_5"]         = over5
            o["line_hits_under_5"]        = under5
            o["line_hits_push_5"]         = push5
            o["line_hit_rate_over_5"]     = round(hr_ov5,    3) if hr_ov5    is not None else ""
            o["line_hit_rate_under_5"]    = round(hr_un5,    3) if hr_un5    is not None else ""
            o["line_hit_rate_over_ou_5"]  = round(hr_ov_ou5, 3) if hr_ov_ou5 is not None else ""
            o["line_hit_rate_under_ou_5"] = round(hr_un_ou5, 3) if hr_un_ou5 is not None else ""

            over10, under10, push10, hr_ov10, hr_un10, hr_ov_ou10, hr_un_ou10 = hit_rates(game_log, ln, 10)
            o["line_hits_over_10"]         = over10
            o["line_hits_under_10"]        = under10
            o["line_hits_push_10"]         = push10
            o["line_hit_rate_over_10"]     = round(hr_ov10,    3) if hr_ov10    is not None else ""
            o["line_hit_rate_under_10"]    = round(hr_un10,    3) if hr_un10    is not None else ""
            o["line_hit_rate_over_ou_10"]  = round(hr_ov_ou10, 3) if hr_ov_ou10 is not None else ""
            o["line_hit_rate_under_ou_10"] = round(hr_un_ou10, 3) if hr_un_ou10 is not None else ""

            o["model_dir_5"] = "OVER" if over5 >= under5 else "UNDER"

        stat_status.append("OK")
        out_rows.append(o)

    stats_df = pd.DataFrame(out_rows).fillna("")
    df["stat_status"] = stat_status
    out = pd.concat([df.reset_index(drop=True), stats_df], axis=1)

    # Re-attach the 2H rows (they carry UNSUPPORTED_2H status, no stat columns)
    if not df_2h.empty:
        out = pd.concat([out, df_2h], ignore_index=True, sort=False).fillna("")

    out.to_csv(args.output, index=False)

    print(f"✅ Saved → {args.output} | rows={len(out)}")
    print("stat_status breakdown:")
    print(out["stat_status"].value_counts().to_string())




if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_pipeline_health(
            "cbb.step5b_attach_boxscore_stats",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        print(f"❌ CBB step5b failed (logged). {type(e).__name__}: {e}")
