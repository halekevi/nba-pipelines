#!/usr/bin/env python3
"""
step1_fetch_prizepicks_tennis.py  (Tennis Pipeline)

Fetches Tennis PrizePicks projections from the public API.
Default league_id=20 (verify on https://api.prizepicks.com/leagues if the board is empty).

Run (from repo root or Tennis/):
  py -3.14 Tennis/scripts/step1_fetch_prizepicks_tennis.py
  py -3.14 Tennis/scripts/step1_fetch_prizepicks_tennis.py --output Tennis/outputs/step1_tennis_props.csv
"""

from __future__ import annotations

import argparse
import json
import re
import time
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Set

import pandas as pd
import requests

API_URL   = "https://api.prizepicks.com/projections"
WARMUP_URL = "https://api.prizepicks.com/leagues"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}


def _make_headers(ua: str) -> dict:
    return {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://app.prizepicks.com",
        "Referer": "https://app.prizepicks.com/board",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _warm_session(session: requests.Session, ua: str) -> None:
    try:
        r = session.get(WARMUP_URL, headers=_make_headers(ua), timeout=15)
        print(f"  🌐 Session warmed ({r.status_code})")
        time.sleep(random.uniform(1.5, 3.0))
    except Exception as e:
        print(f"  ⚠️ Warmup failed: {e} — continuing")


def _safe_get(d: dict, path: List[str], default=""):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _norm_team(s: str) -> str:
    return str(s or "").strip().upper()


def _included_index(included: List[dict]) -> Dict[Tuple[str, str], dict]:
    idx: Dict[Tuple[str, str], dict] = {}
    for obj in included or []:
        t = str(obj.get("type", "")).strip()
        i = str(obj.get("id",   "")).strip()
        if t and i:
            idx[(t, i)] = obj
    return idx


def fetch_pages(
    league_id: str,
    game_mode: str,
    per_page: int,
    max_pages: int,
    sleep: float,
    cooldown_seconds: float,
    max_cooldowns: int,
    jitter_seconds: float,
    max_403_retries: int = 3,
    forbidden_backoff_base: float = 15.0,
) -> Tuple[List[dict], List[dict]]:
    all_data: List[dict] = []
    all_included: List[dict] = []
    cooldowns_used = 0
    forbidden_retries = 0
    stop_paging = False
    seen_ids: Set[str] = set()

    session = requests.Session()
    ua = random.choice(USER_AGENTS)
    headers = _make_headers(ua)
    _warm_session(session, ua)

    for page in range(1, max_pages + 1):
        if stop_paging:
            break
        params = {
            "league_id": str(league_id),
            "game_mode": str(game_mode),
            "per_page": int(per_page),
            "page": int(page),
            "page[number]": int(page),
            "page[size]": int(per_page),
        }
        for attempt in range(1, 9):
            r = session.get(API_URL, headers=headers, params=params, timeout=30)

            if r.status_code == 429:
                cooldowns_used += 1
                if cooldowns_used > max_cooldowns:
                    print(f"🛑 429 persists after {max_cooldowns} cooldowns. Stopping early.")
                    stop_paging = True
                    break
                sleep_s = cooldown_seconds + random.uniform(0, jitter_seconds)
                print(f"⏸️ 429 cooldown {cooldowns_used}/{max_cooldowns}: sleeping {sleep_s:.1f}s...")
                time.sleep(sleep_s)
                continue

            if r.status_code == 403:
                forbidden_retries += 1
                if forbidden_retries > max_403_retries:
                    print(f"🛑 403 persists. Stopping early.")
                    stop_paging = True
                    break
                backoff = forbidden_backoff_base * (2 ** (forbidden_retries - 1)) + random.uniform(2, 8)
                print(f"⏸️ 403 retry {forbidden_retries}/{max_403_retries}: sleeping {backoff:.1f}s...")
                time.sleep(backoff)
                ua = random.choice(USER_AGENTS)
                headers = _make_headers(ua)
                _warm_session(session, ua)
                continue

            if r.status_code >= 500:
                time.sleep(5.0 * attempt)
                continue

            r.raise_for_status()
            j = r.json()
            page_data = j.get("data") or []
            page_new = [x for x in page_data if str(x.get("id","")) not in seen_ids]
            if not page_new:
                print(f"  Page {page}: 0 new rows — stopping pagination")
                stop_paging = True
                break

            for x in page_new:
                seen_ids.add(str(x.get("id","")))
            all_data.extend(page_new)
            all_included.extend(j.get("included") or [])
            print(f"  Page {page}: +{len(page_new)} rows (total={len(all_data)})")
            time.sleep(sleep + random.uniform(0, 0.5))
            break

    session.close()
    return all_data, all_included


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",           default="outputs/step1_tennis_props.csv")
    ap.add_argument("--league_id",        default="20")   # Tennis (PrizePicks; confirm via /leagues if needed)
    ap.add_argument("--game_mode",        default="pickem")
    ap.add_argument("--per_page",         type=int,   default=250)
    ap.add_argument("--max_pages",        type=int,   default=20)
    ap.add_argument("--sleep",            type=float, default=1.2)
    ap.add_argument("--cooldown_seconds", type=float, default=60.0)
    ap.add_argument("--max_cooldowns",    type=int,   default=2)
    ap.add_argument("--jitter_seconds",   type=float, default=7.0)
    ap.add_argument("--max_403_retries",  type=int,   default=3)
    ap.add_argument("--min_rows",         type=int,   default=8)
    ap.add_argument("--min_teams",        type=int,   default=2)
    args = ap.parse_args()

    print(f"📡 Fetching PrizePicks Tennis | league_id={args.league_id}")

    data, included = fetch_pages(
        league_id=args.league_id,
        game_mode=args.game_mode,
        per_page=args.per_page,
        max_pages=args.max_pages,
        sleep=args.sleep,
        cooldown_seconds=args.cooldown_seconds,
        max_cooldowns=args.max_cooldowns,
        jitter_seconds=args.jitter_seconds,
        max_403_retries=args.max_403_retries,
    )

    if not data:
        cols = ["projection_id","pp_projection_id","player_id","pp_game_id","start_time",
                "player","image_url","pos","team","opp_team","pp_home_team","pp_away_team",
                "prop_type","line","pick_type"]
        pd.DataFrame(columns=cols).to_csv(args.output, index=False)
        print("❌ No projections fetched. Wrote empty CSV (check league_id / season).")
        return

    inc = _included_index(included)
    rows: List[dict] = []

    for d in data:
        if not isinstance(d, dict):
            continue
        pid   = str(d.get("id", "")).strip()
        attrs = d.get("attributes") or {}
        rel   = d.get("relationships") or {}

        line      = attrs.get("line_score", attrs.get("line"))
        prop_type = str(attrs.get("stat_type", attrs.get("projection_type", attrs.get("name", "")))).strip()
        odds_type = str(attrs.get("odds_type", "")).strip().lower()
        pick_type = PICKTYPE_MAP.get(odds_type, "Standard")

        player_id   = _safe_get(rel, ["new_player", "data", "id"], "")
        player_type = _safe_get(rel, ["new_player", "data", "type"], "new_player")
        game_id     = _safe_get(rel, ["new_game", "data", "id"], "") or _safe_get(rel, ["game", "data", "id"], "")
        game_type   = _safe_get(rel, ["new_game", "data", "type"], "") or _safe_get(rel, ["game", "data", "type"], "")

        player_obj = inc.get((player_type, str(player_id))) if player_id else None
        game_obj   = inc.get((game_type, str(game_id)))     if game_id and game_type else None

        player_name = pos = team = image_url = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos         = str(pa.get("position", "")).strip()
            team        = _norm_team(pa.get("team", ""))
            image_url   = str(pa.get("image_url") or pa.get("image_url_small") or "").strip()

        home = away = start_time = ""
        if isinstance(game_obj, dict):
            ga = game_obj.get("attributes") or {}
            home       = _norm_team(ga.get("home_team", ""))
            away       = _norm_team(ga.get("away_team", ""))
            start_time = str(ga.get("start_time", "")).strip()

        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        opp_team = ""
        if team and home and away:
            opp_team = away if team == home else (home if team == away else "")
        else:
            desc = str(attrs.get("description", "") or "")
            m = re.search(r"\bvs\.?\s+([A-Za-z]{2,4})\b", desc)
            if m:
                opp_team = _norm_team(m.group(1))

        rows.append({
            "projection_id":    pid,
            "pp_projection_id": pid,
            "player_id":        str(player_id).strip(),
            "pp_game_id":       str(game_id or "").strip(),
            "start_time":       start_time,
            "player":           player_name,
            "image_url":        image_url,
            "pos":              pos,
            "team":             team,
            "opp_team":         opp_team,
            "pp_home_team":     home,
            "pp_away_team":     away,
            "prop_type":        prop_type,
            "line":             line,
            "pick_type":        pick_type,
        })

    df = pd.DataFrame(rows).fillna("")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")

    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"  Deduped: {before} → {after}")

    df.to_csv(args.output, index=False, encoding="utf-8-sig")

    rows_n  = len(df)
    teams_n = df["team"].astype(str).nunique()
    print(f"✅ Saved → {args.output}  rows={rows_n}  teams={teams_n}")

    if rows_n < args.min_rows or teams_n < args.min_teams:
        print(f"⛔ BOARD_TOO_SMALL (need min_rows={args.min_rows}, min_teams={args.min_teams})")
    else:
        print("✅ BOARD_OK")


if __name__ == "__main__":
    main()
