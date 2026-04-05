#!/usr/bin/env python3
"""
Fetch Underdog Fantasy pick'em lines from the public board API and write a
PrizePicks-shaped CSV (plus source_book / Underdog ids).

Endpoint (unauthenticated, browser-like headers):
  GET https://api.underdogfantasy.com/v1/over_under_lines

Sports match Underdog's sport_id on games/solo_games (e.g. NBA, NHL, MLB, CBB,
WCBB, FIFA, TENNIS). Use --sport ALL for every open line.

Examples:
  py -3 scripts/fetch_underdog_pickem.py --sport NBA --output ud_nba.csv
  py -3 scripts/fetch_underdog_pickem.py --sport NBA,NHL,MLB --output ud_multi.csv
  py -3 scripts/fetch_underdog_pickem.py --sport ALL --output ud_all.csv
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from pickem_step1_schema import OUTPUT_COLUMNS

API_URL = "https://api.underdogfantasy.com/v1/over_under_lines"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

HEADERS_BASE = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://underdogfantasy.com",
    "Referer": "https://underdogfantasy.com/",
}

# Map PropORACLE / CLI aliases → Underdog sport_id values present in API payloads.
SPORT_ALIASES: Dict[str, List[str]] = {
    "nba": ["NBA"],
    "nhl": ["NHL"],
    "mlb": ["MLB"],
    "cbb": ["CBB"],
    "wcbb": ["WCBB"],
    "wnba": ["WNBA"],
    "soccer": ["FIFA", "MASL", "UFL"],
    "ufl": ["UFL"],
    "esports": ["CS", "LOL", "VAL", "ESPORTS"],
    "tennis": ["TENNIS"],
}


def _norm_team(s: Any) -> str:
    return str(s or "").strip().upper()


def _parse_game_abbrs(game: dict) -> Tuple[str, str]:
    raw = (game.get("abbreviated_title") or game.get("title") or "").strip()
    raw = raw.split("(")[0].strip()
    if "@" not in raw:
        return "", ""
    away, home = raw.split("@", 1)
    return _norm_team(away), _norm_team(home)


def _player_team_abbr(game: dict, player_team_id: str) -> str:
    if not player_team_id:
        return ""
    hid = str(game.get("home_team_id") or "")
    aid = str(game.get("away_team_id") or "")
    away_abbr, home_abbr = _parse_game_abbrs(game)
    if player_team_id == hid:
        return home_abbr
    if player_team_id == aid:
        return away_abbr
    return ""


def _opp_team(player_abbr: str, home: str, away: str) -> str:
    if not player_abbr or not home or not away:
        return ""
    if player_abbr == home:
        return away
    if player_abbr == away:
        return home
    return ""


def _pick_type(line: dict) -> str:
    nd = line.get("non_discounted_stat_value")
    sv = line.get("stat_value")
    try:
        if nd is not None and sv is not None and float(nd) != float(sv):
            return "Goblin"
    except (TypeError, ValueError):
        pass
    lt = str(line.get("line_type") or "").lower()
    if "discount" in lt or lt == "boosted":
        return "Goblin"
    return "Standard"


def _expand_sports_arg(sports_csv: str) -> Optional[Set[str]]:
    s = sports_csv.strip()
    if not s or s.upper() == "ALL":
        return None
    out: Set[str] = set()
    for part in s.split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key in SPORT_ALIASES:
            out.update(SPORT_ALIASES[key])
        else:
            out.add(key.upper())
    return out


def _fetch_payload(retries: int = 5, timeout: float = 120.0) -> dict:
    last_err: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            h = {**HEADERS_BASE, "User-Agent": random.choice(USER_AGENTS)}
            if attempt > 1:
                time.sleep(random.uniform(1.0, 3.0))
            r = requests.get(API_URL, headers=h, timeout=timeout)
            if r.status_code == 429:
                time.sleep(random.uniform(30.0, 60.0))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(min(30.0, 2**attempt))
    raise RuntimeError(f"Underdog API failed after {retries} tries: {last_err}")


def build_rows(payload: dict, sport_filter: Optional[Set[str]]) -> List[dict]:
    games = {g["id"]: g for g in (payload.get("games") or [])}
    solo = {s["id"]: s for s in (payload.get("solo_games") or [])}
    players = {p["id"]: p for p in (payload.get("players") or [])}
    appearances = payload.get("appearances") or []

    def appearance_sport(app: dict) -> Optional[str]:
        mt = app.get("match_type")
        mid = app.get("match_id")
        if mt == "Game":
            g = games.get(mid)
            return (g or {}).get("sport_id")
        if mt == "SoloGame":
            sg = solo.get(mid)
            return (sg or {}).get("sport_id")
        return None

    allowed_app_ids: Set[str] = set()
    app_by_id: Dict[str, dict] = {}
    for app in appearances:
        aid = str(app.get("id") or "")
        if not aid:
            continue
        sid = appearance_sport(app)
        if sid is None:
            continue
        if sport_filter is None or sid in sport_filter:
            allowed_app_ids.add(aid)
            app_by_id[aid] = app

    rows: List[dict] = []
    for line in payload.get("over_under_lines") or []:
        if str(line.get("status") or "").lower() != "active":
            continue
        ou = line.get("over_under") or {}
        if str(ou.get("category") or "").lower() != "player_prop":
            continue
        ast = ou.get("appearance_stat") or {}
        aid = str(ast.get("appearance_id") or "").strip()
        if not aid or aid not in allowed_app_ids:
            continue

        app = app_by_id.get(aid)
        if not app:
            continue

        pl = players.get(app.get("player_id")) or {}
        first = str(pl.get("first_name") or "").strip()
        last = str(pl.get("last_name") or "").strip()
        player_name = f"{first} {last}".strip()
        pos = str(pl.get("position_name") or pl.get("position_display_name") or "").strip()
        image_url = str(
            pl.get("image_url") or pl.get("light_image_url") or pl.get("dark_image_url") or ""
        ).strip()
        player_team_id = str(pl.get("team_id") or app.get("team_id") or "")

        mt = app.get("match_type")
        mid = app.get("match_id")
        ud_sport: str = ""
        start_time = ""
        home_abbr, away_abbr = "", ""
        pp_game_id = ""

        if mt == "Game":
            g = games.get(mid) or {}
            ud_sport = str(g.get("sport_id") or "")
            start_time = str(g.get("scheduled_at") or "").strip()
            pp_game_id = str(mid)
            away_abbr, home_abbr = _parse_game_abbrs(g)
            team_abbr = _player_team_abbr(g, player_team_id)
        elif mt == "SoloGame":
            sg = solo.get(mid) or {}
            ud_sport = str(sg.get("sport_id") or "")
            start_time = str(sg.get("scheduled_at") or "").strip()
            pp_game_id = f"solo_{mid}"
            team_abbr = ""
            home_abbr, away_abbr = "", ""
        else:
            continue

        opp = _opp_team(team_abbr, home_abbr, away_abbr) if team_abbr else ""

        prop_type = str(ast.get("display_stat") or ast.get("stat") or "").strip()
        stat_key = str(ast.get("stat") or "").strip()
        line_val = line.get("stat_value")

        line_id = str(line.get("stable_id") or line.get("id") or "").strip()

        rows.append(
            {
                "projection_id": line_id,
                "pp_projection_id": line_id,
                "player_id": str(pl.get("id") or "").strip(),
                "pp_game_id": pp_game_id,
                "start_time": start_time,
                "player": player_name,
                "pos": pos,
                "team": team_abbr,
                "opp_team": opp,
                "prop_type": prop_type,
                "line": line_val,
                "pick_type": _pick_type(line),
                "pp_home_team": home_abbr,
                "pp_away_team": away_abbr,
                "image_url": image_url,
                "source_book": "underdog",
                "ud_sport_id": ud_sport,
                "ud_line_id": str(line.get("id") or "").strip(),
                "ud_stat_key": stat_key,
            }
        )

    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Underdog pick'em → PP-shaped CSV")
    ap.add_argument(
        "--sport",
        default="ALL",
        help="Comma list or ALL. Aliases: nba,nhl,mlb,cbb,wcbb,wnba,soccer,ufl,esports,tennis",
    )
    ap.add_argument("--output", default="step1_underdog_props.csv")
    ap.add_argument("--cache-json", default="", help="Optional path to save raw API JSON")
    ap.add_argument("--min-rows", type=int, default=1)
    args = ap.parse_args()

    filt = _expand_sports_arg(args.sport)
    print(f"[underdog] pick'em | sport={args.sport!r} | filter={filt or 'ALL'}")

    try:
        payload = _fetch_payload()
    except Exception as e:
        print(f"[err] Fetch failed: {e}")
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    if args.cache_json:
        try:
            with open(args.cache_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            print(f"[cache] Raw JSON -> {args.cache_json}")
        except OSError as e:
            print(f"  [WARN] cache-json: {e}")

    rows = build_rows(payload, filt)
    df = pd.DataFrame(rows)
    if df.empty:
        print("[warn] No rows after filter - writing empty CSV")
        df = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        df["line"] = pd.to_numeric(df["line"], errors="coerce")
        df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
        df = df[OUTPUT_COLUMNS]

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"[ok] {len(df)} rows -> {args.output}")

    if len(df) < args.min_rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
