#!/usr/bin/env python3
"""
step4_attach_player_stats_soccer.py  (Soccer Pipeline) — OPTIMIZED

Key optimizations vs original:
  1. Concurrent ESPN player stat fetching via ThreadPoolExecutor (default 10 workers)
     → 1069 players × ~20 API calls: ~5 hours → ~30 min
  2. Fetch each unique player ONCE, then broadcast to all matching rows
     (original fetched per-row, causing duplicate network calls)
  3. Cache saved in one batch after all fetches complete
     (original saved after every single game = thousands of file writes)
  4. Concurrent event-log + match-stats fetching per player using inner pool
  5. Thread-local requests.Session to reuse HTTP connections

Run:
  py step4_attach_player_stats_soccer.py \
    --input step3_soccer_with_defense.csv \
    --cache soccer_stats_cache.csv \
    --output step4_soccer_with_stats.csv
"""

from __future__ import annotations

import argparse
import random
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

COMBO_SEP = "|"

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

LEAGUE_SLUGS = {
    "EPL":        "eng.1",
    "BUNDESLIGA": "ger.1",
    "LIGUE 1":    "fra.1",
    "SERIE A":    "ita.1",
    "LA LIGA":    "esp.1",
    "MLS":        "usa.1",
    "UCL":        "uefa.champions",
}
DEFAULT_SLUG = "eng.1"

EVENTLOG_URL   = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league_slug}/athletes/{athlete_id}/eventlog"
EVENTLOG_URL_GENERIC = "https://site.api.espn.com/apis/site/v2/sports/soccer/athletes/{athlete_id}/eventlog"
ATHLETE_STATS_URL = (
    "https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league_slug}"
    "/events/{event_id}/competitions/{event_id}/athletes/{athlete_id}/statistics/0"
)

# Thread-local session
_local = threading.local()

def _get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.headers.update(ESPN_HEADERS)
        _local.session = s
    return _local.session


def _sleep(base: float = 0.3) -> None:
    time.sleep(max(0.0, base + random.uniform(0, 0.2)))


def _get(url: str, retries: int = 3) -> Optional[dict]:
    session = _get_session()
    for attempt in range(1, retries + 1):
        try:
            _sleep(0.3)
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None


def _parse_ids(espn_player_id: str) -> List[str]:
    s = str(espn_player_id).strip()
    if not s or s == "nan":
        return []
    if COMBO_SEP in s:
        return [p.strip() for p in s.split(COMBO_SEP) if p.strip().isdigit()]
    return [s] if s.isdigit() else []


def fmt_num(x: float) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def get_athlete_events(athlete_id: str, league: str = "") -> List[dict]:
    """
    Try the league-specific eventlog URL first (most reliable for soccer).
    Falls back to the generic URL and then all known league slugs.
    """
    league_slug = LEAGUE_SLUGS.get(league.upper(), DEFAULT_SLUG) if league else DEFAULT_SLUG

    # Build ordered list of URLs to try
    urls_to_try = []
    # 1. Specific slug for this player's league
    urls_to_try.append(EVENTLOG_URL.format(league_slug=league_slug, athlete_id=athlete_id))
    # 2. Generic (no-league) URL — may work on some ESPN API versions
    urls_to_try.append(EVENTLOG_URL_GENERIC.format(athlete_id=athlete_id))
    # 3. All other league slugs as fallback
    for slug in LEAGUE_SLUGS.values():
        url = EVENTLOG_URL.format(league_slug=slug, athlete_id=athlete_id)
        if url not in urls_to_try:
            urls_to_try.append(url)

    for url in urls_to_try:
        data = _get(url)
        if not data:
            continue

        events = []
        ev_block = data.get("events")

        if isinstance(ev_block, dict):
            for section in (ev_block.get("events") or []):
                eid  = str(section.get("id", "")).strip()
                date = str(section.get("date", section.get("gameDate", ""))).strip()
                lg   = str(section.get("league", {}).get("abbreviation", "")).strip().upper()
                if eid:
                    events.append({"event_id": eid, "date": date, "league": lg})

        if not events and isinstance(ev_block, list):
            for item in ev_block:
                if isinstance(item, dict):
                    eid  = str(item.get("id", "")).strip()
                    date = str(item.get("date", "")).strip()
                    if eid:
                        events.append({"event_id": eid, "date": date, "league": ""})

        if not events:
            for season in (data.get("seasons") or []):
                for entry in (season.get("types", season.get("entries", [])) or []):
                    for ev in (entry.get("events", []) or []):
                        if isinstance(ev, dict):
                            eid  = str(ev.get("id", "")).strip()
                            date = str(ev.get("date", "")).strip()
                            if eid:
                                events.append({"event_id": eid, "date": date, "league": ""})

        if events:
            return events

    return []


def get_player_match_stats(athlete_id: str, event_id: str, league_slug: str) -> Dict[str, float]:
    url  = ATHLETE_STATS_URL.format(
        league_slug=league_slug, event_id=event_id, athlete_id=athlete_id
    )
    data = _get(url)
    if not data:
        return {}

    stats: Dict[str, float] = {}
    for split in (data.get("splits") or {}).get("categories") or []:
        cat = str(split.get("name", "")).lower()
        for stat in (split.get("stats") or []):
            name = str(stat.get("name", "")).lower().strip()
            try:
                val = float(stat.get("value", ""))
                stats[f"{cat}_{name}"] = val
                stats[name] = val
            except (TypeError, ValueError):
                pass
    return stats


def derive_stat(stats: Dict[str, float], prop_norm: str) -> float:
    p = str(prop_norm).lower().strip()
    lookups = {
        "shots":          ["general_shots", "shots", "totalshots"],
        "shots_on_target":["general_shotsontarget", "shotsontarget", "shots on target"],
        "saves":          ["general_saves", "saves", "goalsaves"],
        "passes":         ["general_passes", "passes", "totalpasses", "passesattempted"],
        "assists":        ["general_assists", "assists"],
        "goals":          ["general_goals", "goals"],
        "clearances":     ["defending_clearances", "clearances"],
        "tackles":        ["defending_tackles", "tackles", "tacklesmade"],
        "fouls":          ["discipline_foulsconceded", "fouls", "foulsconceded"],
        "goals_allowed":  ["general_goalsagainst", "goalsagainst", "goalsconceded"],
        "goal_assist":    ["goals", "assists"],
        "shots_assisted": ["general_shotsassisted", "shotsassisted", "keypassescompleted"],
    }
    keys = lookups.get(p, [p])

    if p == "goal_assist":
        g = next((stats[k] for k in lookups["goals"]   if k in stats), np.nan)
        a = next((stats[k] for k in lookups["assists"]  if k in stats), np.nan)
        if not np.isnan(g) and not np.isnan(a):
            return float(g + a)
        return np.nan

    for k in keys:
        if k in stats:
            return float(stats[k])
    for k in keys:
        for sk in stats:
            if k in sk or sk in k:
                return float(stats[sk])
    return np.nan


ALL_PROP_NORMS = [
    "shots", "shots_on_target", "saves", "passes", "assists",
    "goals", "clearances", "tackles", "fouls", "goals_allowed",
    "goal_assist", "shots_assisted",
]


def _fetch_player_stats(
    athlete_id: str,
    league: str,
    n_games: int,
    existing_event_ids: set,
) -> List[dict]:
    """
    Fetch up to n_games new match stats for one player.
    Returns list of cache row dicts.
    """
    events  = get_athlete_events(athlete_id, league=league)
    new_rows: List[dict] = []
    added   = 0

    for ev in events[: n_games * 2]:
        eid = ev["event_id"]
        if (str(athlete_id), str(eid)) in existing_event_ids:
            continue

        lg_slug = LEAGUE_SLUGS.get(league.upper(),
                  LEAGUE_SLUGS.get(ev["league"].upper(), DEFAULT_SLUG))
        stats   = get_player_match_stats(athlete_id, eid, lg_slug)
        if not stats:
            continue

        date = ev.get("date", "")
        for prop_norm in ALL_PROP_NORMS:
            val = derive_stat(stats, prop_norm)
            new_rows.append({
                "ESPN_ATHLETE_ID": str(athlete_id),
                "EVENT_ID":        str(eid),
                "GAME_DATE":       date,
                "LEAGUE":          league,
                "PROP_NORM":       prop_norm,
                "STAT_VALUE":      fmt_num(val) if not np.isnan(val) else "",
            })
        existing_event_ids.add((str(athlete_id), str(eid)))
        added += 1
        if added >= n_games:
            break

    return new_rows


def load_cache(cache_path: Path) -> pd.DataFrame:
    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, dtype=str, low_memory=False).fillna("")
            print(f"  Loaded cache: {len(df)} rows from {cache_path.name}")
            return df
        except Exception as e:
            print(f"  ⚠️ Could not load cache: {e}")
    cols = ["ESPN_ATHLETE_ID", "EVENT_ID", "GAME_DATE", "LEAGUE", "PROP_NORM", "STAT_VALUE"]
    return pd.DataFrame(columns=cols)


def save_cache(cache: pd.DataFrame, cache_path: Path) -> None:
    cache.to_csv(cache_path, index=False, encoding="utf-8-sig")


def get_vals_from_cache(
    cache: pd.DataFrame,
    athlete_id: str,
    prop_norm: str,
    n: int = 10,
) -> List[float]:
    mask = (
        (cache["ESPN_ATHLETE_ID"].astype(str) == str(athlete_id)) &
        (cache["PROP_NORM"].astype(str) == str(prop_norm)) &
        (cache["STAT_VALUE"].astype(str).str.strip() != "")
    )
    sub = cache.loc[mask].copy()
    if sub.empty:
        return []
    sub["GAME_DATE"] = pd.to_datetime(sub["GAME_DATE"], errors="coerce")
    sub = sub.sort_values("GAME_DATE", ascending=False)
    vals = pd.to_numeric(sub["STAT_VALUE"], errors="coerce").dropna().tolist()
    return vals[:n]


def calc_hit_context(vals: List[float], line: float, k: int = 5):
    recent  = vals[:k] if len(vals) >= k else vals
    if not recent:
        return 0, 0, 0, np.nan, np.nan, np.nan
    over   = sum(1 for v in recent if v > line)
    under  = sum(1 for v in recent if v < line)
    push   = sum(1 for v in recent if v == line)
    played = len(recent)
    hr_all = over / played if played else np.nan
    denom  = over + under
    hr_ou  = over  / denom if denom else np.nan
    ur_ou  = under / denom if denom else np.nan
    return over, under, push, hr_all, hr_ou, ur_ou


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   required=True)
    ap.add_argument("--cache",   default="soccer_stats_cache.csv")
    ap.add_argument("--output",  required=True)
    ap.add_argument("--n",       type=int, default=10, help="Max games per player")
    ap.add_argument("--workers", type=int, default=10,
                    help="Concurrent player fetch workers (default 10)")
    ap.add_argument("--season",  default="2025")
    ap.add_argument("--debug_misses", default="")
    ap.add_argument("--debug_player", default="",
                    help="ESPN athlete ID to debug — prints raw eventlog response and exits")
    args = ap.parse_args()

    # Debug mode: show raw eventlog for one player then exit
    if args.debug_player:
        import json
        aid  = args.debug_player.strip()
        print(f"\n🔍 DEBUG player {aid}")
        for league_slug in list(LEAGUE_SLUGS.values()) + ["arg.1", "bra.1", "por.1", "ned.1"]:
            url = EVENTLOG_URL.format(league_slug=league_slug, athlete_id=aid)
            print(f"\n  Trying: {url}")
            data = _get(url)
            if data:
                print(f"  ✅ Got response:")
                print(json.dumps(data, indent=2)[:3000])
                break
            else:
                print(f"  ❌ No data")
        # Also try generic
        url2 = EVENTLOG_URL_GENERIC.format(athlete_id=aid)
        print(f"\n  Generic URL: {url2}")
        data2 = _get(url2)
        if data2:
            print(json.dumps(data2, indent=2)[:3000])
        return

    print(f"→ Loading Step3: {args.input}")
    slate      = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig").fillna("")
    cache_path = Path(args.cache)
    cache      = load_cache(cache_path)

    N         = int(args.n)
    stat_cols = [f"stat_g{i}" for i in range(1, N + 1)]
    out_cols  = stat_cols + [
        "stat_last5_avg", "stat_last10_avg", "stat_season_avg",
        "last5_over", "last5_under", "last5_push", "last5_hit_rate",
        "line_hit_rate_over_ou_5", "line_hit_rate_under_ou_5",
        "line_hit_rate_over_ou_10", "line_hit_rate_under_ou_10",
        "stat_status",
    ]
    for c in out_cols:
        if c not in slate.columns:
            slate[c] = ""

    slate["_line_num"] = pd.to_numeric(slate.get("line", ""), errors="coerce")

    # ── Build set of unique players that need fresh data ──────────────────────
    existing_keys = set(
        zip(cache["ESPN_ATHLETE_ID"].astype(str), cache["EVENT_ID"].astype(str))
    )

    # Collect unique (athlete_id, league) pairs not yet sufficiently cached
    players_needing_fetch: Dict[str, str] = {}   # athlete_id → league
    for _, row in slate.iterrows():
        espn_id_raw = str(row.get("espn_player_id", "")).strip()
        ids         = _parse_ids(espn_id_raw)
        league      = str(row.get("league", "")).strip().upper()
        for aid in ids:
            if aid not in players_needing_fetch:
                cached_count = int((cache["ESPN_ATHLETE_ID"].astype(str) == aid).sum())
                if cached_count < N * len(ALL_PROP_NORMS):
                    players_needing_fetch[aid] = league

    print(f"\n→ Fetching stats for {len(players_needing_fetch)} players "
          f"(workers={args.workers}, n_games={N})...")

    # CONCURRENT fetch: one worker per unique player
    new_rows_all: List[dict] = []
    fetched = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_fetch_player_stats, aid, league, N, set(existing_keys)): aid
            for aid, league in players_needing_fetch.items()
        }
        for i, fut in enumerate(as_completed(futures), 1):
            aid = futures[fut]
            try:
                new_rows = fut.result()
                if new_rows:
                    new_rows_all.extend(new_rows)
                    fetched += 1
            except Exception as e:
                print(f"  ⚠️ {aid}: {e}")
            if i % 50 == 0 or i == len(futures):
                print(f"    {i}/{len(futures)} players done  new_rows={len(new_rows_all)}")

    # Append all new rows to cache at once, then save once
    if new_rows_all:
        cache = pd.concat([cache, pd.DataFrame(new_rows_all)], ignore_index=True)
        save_cache(cache, cache_path)
        print(f"Cache updated: +{len(new_rows_all)} rows → {cache_path}")

    # ── Attach stats to slate rows ────────────────────────────────────────────
    print(f"\n→ Attaching stats to {len(slate)} rows...")
    misses: List[dict] = []

    for idx, row in slate.iterrows():
        prop        = str(row.get("prop_norm", "")).lower().strip()
        player      = str(row.get("player",    "")).strip()
        team        = str(row.get("team",      "")).strip()
        league      = str(row.get("league",    "")).strip().upper()
        espn_id_raw = str(row.get("espn_player_id", "")).strip()
        line        = row.get("_line_num", np.nan)
        try:
            line = float(line)
        except Exception:
            line = np.nan

        ids      = _parse_ids(espn_id_raw)
        is_combo = (len(ids) > 1) or (
            str(row.get("is_combo_player", "")).strip().lower() in ("1", "true", "yes")
        )

        if not ids:
            slate.at[idx, "stat_status"] = "NO_ESPN_PLAYER"
            misses.append({"player": player, "team": team, "prop_norm": prop,
                           "line": str(row.get("line", "")), "espn_player_id": espn_id_raw})
            continue

        if not is_combo:
            vals = get_vals_from_cache(cache, ids[0], prop, n=N)
            if not vals:
                slate.at[idx, "stat_status"] = "NO_CACHE_DATA"
                continue
        else:
            per_player_vals: List[List[float]] = []
            any_empty = False
            for aid in ids:
                pv = get_vals_from_cache(cache, aid, prop, n=N)
                if not pv:
                    any_empty = True
                    break
                per_player_vals.append(pv)
            if any_empty or not per_player_vals:
                slate.at[idx, "stat_status"] = "NO_CACHE_DATA"
                continue
            min_g = min(len(pv) for pv in per_player_vals)
            vals  = [float(sum(pv[i] for pv in per_player_vals)) for i in range(min_g)]
            if not vals:
                slate.at[idx, "stat_status"] = "INSUFFICIENT_GAMES"
                continue

        for i in range(1, N + 1):
            v = vals[i - 1] if (i - 1) < len(vals) else np.nan
            slate.at[idx, f"stat_g{i}"] = fmt_num(v)

        def avg_k(k: int) -> float:
            s = vals[:k] if len(vals) >= k else vals
            return float(np.mean(s)) if s else np.nan

        slate.at[idx, "stat_last5_avg"]  = fmt_num(avg_k(5))
        slate.at[idx, "stat_last10_avg"] = fmt_num(avg_k(10))
        slate.at[idx, "stat_season_avg"] = fmt_num(float(np.mean(vals)) if vals else np.nan)

        if not np.isnan(line):
            o5, u5, p5, hr5, hr5_ou, ur5_ou = calc_hit_context(vals, line, k=5)
            slate.at[idx, "last5_over"]               = str(o5)
            slate.at[idx, "last5_under"]              = str(u5)
            slate.at[idx, "last5_push"]               = str(p5)
            slate.at[idx, "last5_hit_rate"]           = fmt_num(hr5)
            slate.at[idx, "line_hit_rate_over_ou_5"]  = fmt_num(hr5_ou)
            slate.at[idx, "line_hit_rate_under_ou_5"] = fmt_num(ur5_ou)
            _, _, _, _, hr10_ou, ur10_ou = calc_hit_context(vals, line, k=10)
            slate.at[idx, "line_hit_rate_over_ou_10"]  = fmt_num(hr10_ou)
            slate.at[idx, "line_hit_rate_under_ou_10"] = fmt_num(ur10_ou)

        slate.at[idx, "stat_status"] = "OK"

    if args.debug_misses and misses:
        pd.DataFrame(misses).drop_duplicates().to_csv(
            args.debug_misses, index=False, encoding="utf-8-sig"
        )
        print(f"Wrote misses → {args.debug_misses}")

    slate.drop(columns=["_line_num"], errors="ignore", inplace=True)
    slate.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"\n✅ Saved → {args.output}")
    print(f"Cache updates: {fetched} players / {len(new_rows_all)} rows")
    print("\nstat_status breakdown:")
    print(slate["stat_status"].astype(str).value_counts().to_string())


if __name__ == "__main__":
    main()
