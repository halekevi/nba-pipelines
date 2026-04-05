#!/usr/bin/env python3
"""
step1_fetch_prizepicks_api.py  (NBA Pipeline A - direct API edition)

Fetches PrizePicks projections directly from the API — no browser, no
Playwright, no interception. Harder to detect, faster, and fully headless.

Strategy:
  - Rotates User-Agent strings per request
  - Uses a persistent requests.Session with realistic headers
  - Paginates through all projections (per_page=250)
  - Retries with exponential backoff on 429/5xx
  - Validates output row/team counts before writing
  - Exits non-zero if data is missing so the pipeline halts cleanly

Outputs: step1_pp_props_today.csv  (same schema as before)
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests

# ── constants ─────────────────────────────────────────────────────────────────

BASE_URL = "https://api.prizepicks.com/projections"

PICKTYPE_MAP = {
    "standard": "Standard",
    "goblin":   "Goblin",
    "demon":    "Demon",
}

# Realistic browser User-Agents — rotated per session
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# Base headers that look like a real browser
BASE_HEADERS = {
    "Accept":             "application/json, text/plain, */*",
    "Accept-Language":    "en-US,en;q=0.9",
    "Accept-Encoding":    "gzip, deflate, br",
    "Connection":         "keep-alive",
    "Referer":            "https://app.prizepicks.com/",
    "Origin":             "https://app.prizepicks.com",
    "Sec-Fetch-Dest":     "empty",
    "Sec-Fetch-Mode":     "cors",
    "Sec-Fetch-Site":     "same-site",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "DNT":                "1",
}

# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_get(d: Any, path: list, default: Any = "") -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _norm_team(s: Any) -> str:
    return str(s or "").strip().upper()


def _make_session() -> requests.Session:
    """Create a session with realistic headers and a random User-Agent."""
    s = requests.Session()
    ua = random.choice(USER_AGENTS)
    s.headers.update({**BASE_HEADERS, "User-Agent": ua})
    # Brief pause before first request — looks more human
    time.sleep(random.uniform(0.5, 1.5))
    return s


def _api_get(
    session: requests.Session,
    url: str,
    params: dict,
    retries: int = 5,
    timeout: Tuple[float, float] = (10.0, 30.0),
) -> dict:
    """Stateless GET — builds URL manually to avoid params-encoding 403 issues."""
    import urllib.parse
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}"
    WORKING_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://app.prizepicks.com/",
        "Origin":     "https://app.prizepicks.com",
    }
    """
    GET with retry logic:
      - 429 → long backoff (60-120s) then retry
      - 5xx → exponential backoff
      - Other errors → exponential backoff
    Raises RuntimeError after all retries exhausted.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            # Small jitter between every request
            if attempt > 1:
                time.sleep(random.uniform(1.0, 3.0))

            r = requests.get(full_url, headers=WORKING_HEADERS, timeout=timeout)

            if r.status_code == 429:
                wait = random.uniform(60.0, 120.0)
                print(f"  [429] Rate limited — waiting {wait:.0f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue

            if r.status_code == 403:
                print(f"  [403] Forbidden on attempt {attempt}/{retries} — rotating headers")
                # Swap User-Agent and retry
                session.headers.update({"User-Agent": random.choice(USER_AGENTS)})
                time.sleep(random.uniform(5.0, 15.0))
                continue

            if r.status_code >= 500:
                wait = min(60.0, (2 ** (attempt - 1)) * 3.0) + random.uniform(1.0, 4.0)
                print(f"  [{r.status_code}] Server error — waiting {wait:.1f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.ConnectionError as e:
            wait = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0)
            print(f"  [CONN] Connection error attempt {attempt}/{retries}: {e} — waiting {wait:.1f}s")
            last_exc = e
            time.sleep(wait)
        except requests.exceptions.Timeout as e:
            wait = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0)
            print(f"  [TIMEOUT] Timeout attempt {attempt}/{retries} — waiting {wait:.1f}s")
            last_exc = e
            time.sleep(wait)
        except Exception as e:
            wait = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0)
            print(f"  [ERR] Unexpected error attempt {attempt}/{retries}: {type(e).__name__}: {e}")
            last_exc = e
            time.sleep(wait)

    raise RuntimeError(f"API GET failed after {retries} retries: {url} | last={last_exc}")


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_projections(
    league_id: str,
    per_page: int = 250,
    max_pages: int = 10,
    retries: int = 5,
) -> Tuple[List[dict], List[dict]]:
    """
    Fetch all projections + included sideloads from PrizePicks API.
    Paginates until no more data or max_pages reached.
    Returns (data_list, included_list).
    """
    session = _make_session()

    all_data: List[dict] = []
    all_included: List[dict] = []
    seen_ids: set = set()

    params = {
        "league_id":   league_id,
        "per_page":    per_page,
        "single_stat": "true",
        "in_game":     "false",
    }

    print(f"  Fetching page 1 (league_id={league_id}, per_page={per_page})...")
    payload = _api_get(session, BASE_URL, params, retries=retries)

    data     = payload.get("data") or []
    included = payload.get("included") or []

    for obj in data:
        oid = str(obj.get("id", ""))
        if oid not in seen_ids:
            all_data.append(obj)
            seen_ids.add(oid)
    all_included.extend(included)
    print(f"    page 1 → {len(data)} projections")

    # Check for pagination — some API responses include links.next
    links = payload.get("links") or {}
    page = 2
    while links.get("next") and page <= max_pages:
        next_url = links["next"]
        print(f"  Fetching page {page}...")
        # Small inter-page delay
        time.sleep(random.uniform(1.5, 3.0))
        try:
            payload  = _api_get(session, next_url, {}, retries=retries)
            new_data = payload.get("data") or []
            new_inc  = payload.get("included") or []
            added = 0
            for obj in new_data:
                oid = str(obj.get("id", ""))
                if oid not in seen_ids:
                    all_data.append(obj)
                    seen_ids.add(oid)
                    added += 1
            all_included.extend(new_inc)
            print(f"    page {page} → {len(new_data)} projections ({added} new)")
            links = payload.get("links") or {}
            if not new_data:
                break
        except Exception as e:
            print(f"  [WARN] Page {page} failed: {e} — stopping pagination")
            break
        page += 1

    session.close()
    return all_data, all_included


# ── parse ─────────────────────────────────────────────────────────────────────

def _included_index(included: List[dict]) -> Dict[Tuple[str, str], dict]:
    idx: Dict[Tuple[str, str], dict] = {}
    for obj in included or []:
        t = str(obj.get("type", "")).strip()
        i = str(obj.get("id", "")).strip()
        if t and i:
            idx[(t, i)] = obj
    return idx


def build_rows(data: List[dict], included: List[dict]) -> List[dict]:
    inc = _included_index(included)
    rows = []

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
        std_api = attrs.get("standard_line") or attrs.get("standard_score") or attrs.get("baseline")
        if pick_type == "Standard":
            standard_line = std_api if std_api is not None and str(std_api).strip() != "" else line
        else:
            standard_line = std_api if std_api is not None else ""

        # Player
        player_id   = _safe_get(rel, ["new_player", "data", "id"], "") or ""
        player_type = _safe_get(rel, ["new_player", "data", "type"], "new_player")
        player_obj  = inc.get((str(player_type), str(player_id))) if player_id else None

        player_name = pos = team = image_url = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos         = str(pa.get("position", "")).strip()
            team        = _norm_team(pa.get("team", ""))
            image_url   = str(
                pa.get("image_url") or pa.get("image_url_small") or
                pa.get("photo_url") or pa.get("headshot") or
                pa.get("avatar") or ""
            ).strip()

        # Game
        game_id   = _safe_get(rel, ["new_game", "data", "id"], "") or _safe_get(rel, ["game", "data", "id"], "")
        game_type = _safe_get(rel, ["new_game", "data", "type"], "") or _safe_get(rel, ["game", "data", "type"], "")
        game_obj  = inc.get((str(game_type), str(game_id))) if game_id and game_type else None

        home = away = start_time = ""
        if isinstance(game_obj, dict):
            ga         = game_obj.get("attributes") or {}
            home       = _norm_team(ga.get("home_team", ""))
            away       = _norm_team(ga.get("away_team", ""))
            start_time = str(ga.get("start_time", "")).strip()

        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        # Opponent — step2 will also infer this from pp_game_id, but populate if possible
        opp_team = ""
        if team and home and away:
            opp_team = away if team == home else (home if team == away else "")
        elif not opp_team:
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
            "pos":              pos,
            "team":             team,
            "opp_team":         opp_team,
            "prop_type":        prop_type,
            "line":             line,
            "standard_line":    standard_line,
            "pick_type":        pick_type,
            "pp_home_team":     home,
            "pp_away_team":     away,
            "image_url":        image_url,
        })

    return rows


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch PrizePicks props — direct API, no browser")
    ap.add_argument("--output",     default="step1_pp_props_today.csv")
    ap.add_argument("--league_id",  default="7")
    ap.add_argument("--per_page",   type=int, default=250)
    ap.add_argument("--max_pages",  type=int, default=10)
    ap.add_argument("--retries",    type=int, default=5)
    ap.add_argument("--min_rows",   type=int, default=50,  help="Minimum props required to consider fetch valid")
    ap.add_argument("--min_teams",  type=int, default=4,   help="Minimum teams required to consider fetch valid")
    ap.add_argument("--raw_json",   default="",            help="Optional path to dump raw API response")
    ap.add_argument("--history",    default="",            help="Optional path template for history CSV (use {ts})")
    # Legacy args accepted but ignored (were for Playwright version)
    ap.add_argument("--game_mode",        default="pickem")
    ap.add_argument("--sleep",            type=float, default=2.0)
    ap.add_argument("--cooldown_seconds", type=float, default=90.0)
    ap.add_argument("--max_cooldowns",    type=int,   default=3)
    ap.add_argument("--jitter_seconds",   type=float, default=10.0)
    args = ap.parse_args()

    EMPTY_COLS = [
        "projection_id", "pp_projection_id", "player_id", "pp_game_id",
        "start_time", "player", "pos", "team", "opp_team", "prop_type",
        "line", "standard_line", "pick_type", "pp_home_team", "pp_away_team", "image_url",
    ]

    print(f"📡 PrizePicks fetch | league_id={args.league_id} | direct API (no browser)")

    try:
        data, included = fetch_projections(
            league_id=str(args.league_id),
            per_page=args.per_page,
            max_pages=args.max_pages,
            retries=args.retries,
        )
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
        pd.DataFrame(columns=EMPTY_COLS).to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    if not data:
        print("❌ No projections returned from API.")
        pd.DataFrame(columns=EMPTY_COLS).to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    # Optional raw JSON dump
    if args.raw_json:
        try:
            with open(args.raw_json, "w", encoding="utf-8") as f:
                json.dump({"data": data, "included": included}, f, ensure_ascii=False)
            print(f"🧾 Raw JSON saved → {args.raw_json}")
        except Exception as e:
            print(f"  [WARN] raw_json write failed: {e}")

    rows = build_rows(data, included)
    df   = pd.DataFrame(rows).fillna("")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df["standard_line"] = pd.to_numeric(df["standard_line"], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line"])

    # Dedupe
    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    if before != len(df):
        print(f"  Deduped: {before} → {len(df)} rows")

    # Enforce column order
    df = df[EMPTY_COLS].copy()

    # ── Validation ────────────────────────────────────────────────────────────
    n_rows  = len(df)
    n_teams = df["team"].astype(str).replace("", pd.NA).dropna().nunique()

    print(f"\n✅ Fetched {n_rows} props across {n_teams} teams")
    print(f"   Pick types : {df['pick_type'].value_counts().to_dict()}")
    print(f"   Prop types : {df['prop_type'].nunique()} unique")

    if n_rows < args.min_rows or n_teams < args.min_teams:
        print(f"\n⛔ BOARD_TOO_SMALL — got {n_rows} rows / {n_teams} teams")
        print(f"   Required: min_rows={args.min_rows}, min_teams={args.min_teams}")
        print(f"   Writing partial CSV and exiting with error so pipeline halts.")
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    # ── Write output ──────────────────────────────────────────────────────────
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"\n✅ Saved → {args.output}")

    # Optional history snapshot
    if args.history:
        try:
            ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
            hist_path = args.history.replace("{ts}", ts)
            df.to_csv(hist_path, index=False, encoding="utf-8-sig")
            print(f"   History → {hist_path}")
        except Exception as e:
            print(f"  [WARN] History write failed: {e}")

    print("\n✅ BOARD_OK")


if __name__ == "__main__":
    main()
