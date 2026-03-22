#!/usr/bin/env python3
"""
Step 2 — Normalize Props + Resolve NHL Player IDs
- Normalizes stat_type names
- Classifies players as SKATER or GOALIE
- Resolves NHL player IDs (cache-first)
- Uses suggest endpoint first; falls back to search.d3.nhle.com
- Supports combo players "A + B" -> "idA|idB"
- Saves persistent id cache to nhl_id_cache.csv

Usage:
    py .\step2_attach_picktypes_nhl.py --input .\step1_nhl_props.csv --output .\step2_nhl_picktypes.csv --cache .\nhl_id_cache.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

# Stat type normalization
STAT_NORM = {
    "Shots On Goal": "shots_on_goal",
    "Shots on Goal": "shots_on_goal",
    "shots_on_goal": "shots_on_goal",
    "Goals": "goals",
    "goals": "goals",
    "Assists": "assists",
    "assists": "assists",
    "Points": "points",
    "points": "points",
    "Hits": "hits",
    "hits": "hits",
    "Blocked Shots": "blocked_shots",
    "Blocked shots": "blocked_shots",
    "blocked_shots": "blocked_shots",
    "Fantasy Score": "fantasy_score",
    "fantasy_score": "fantasy_score",
    "Saves": "saves",
    "saves": "saves",
    "Goals Allowed": "goals_allowed",
    "goals_allowed": "goals_allowed",
}

# Position classification
GOALIE_POSITIONS = {"G", "Goalie", "Goaltender"}
GOALIE_PROPS = {"saves", "goals_allowed"}

# Combo splitter (PrizePicks style often "A + B")
COMBO_SPLIT_RE = re.compile(r"\s*\+\s*")


def fetch_json(url: str, retries: int = 3, timeout: int = 15) -> dict | list | None:
    """
    Safe fetch:
      - never raises to caller
      - short retries
    """
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
            if attempt == retries - 1:
                return None
            time.sleep(0.8 + attempt * 0.6)
        except Exception:
            return None
    return None


def load_cache(cache_path: str) -> dict:
    cache: dict[str, str] = {}
    if os.path.exists(cache_path):
        with open(cache_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                k = (row.get("player_name") or "").strip().lower()
                v = (row.get("nhl_player_id") or "").strip()
                if k:
                    cache[k] = v
    return cache


def save_cache(cache: dict, cache_path: str):
    with open(cache_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_name", "nhl_player_id"])
        writer.writeheader()
        for name, pid in sorted(cache.items()):
            writer.writerow({"player_name": name, "nhl_player_id": pid})


def _parse_suggest_payload(data, original_name: str) -> str:
    """
    Suggest format examples:
      {"suggestions":["8478402|McDavid|Connor|1|EDM|C|..."]}
    We return the first suggestion's leading ID if present.
    """
    if not data or not isinstance(data, dict):
        return ""
    suggestions = data.get("suggestions", []) or []
    if not suggestions:
        return ""
    first = str(suggestions[0])
    parts = first.split("|")
    if parts and parts[0].isdigit():
        return parts[0]
    return ""


def _search_suggest(name: str) -> str:
    encoded = urllib.parse.quote(name)
    url = f"https://suggest.svc.nhl.com/svc/suggest/v1/minplayers/{encoded}/99"
    data = fetch_json(url)
    return _parse_suggest_payload(data, name)


def _search_d3(name: str) -> str:
    """
    Fallback endpoint:
      https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=25&q=<name>
    Common response: {"data":[{"playerId":..., "name":"..."}]}
    """
    encoded = urllib.parse.quote(name)
    url = f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=25&q={encoded}"
    data = fetch_json(url)
    if not data:
        return ""

    items = None
    if isinstance(data, dict):
        items = data.get("data") or data.get("results") or data.get("items")
    elif isinstance(data, list):
        items = data

    if not items or not isinstance(items, list):
        return ""

    target = name.strip().lower()
    best = None
    for it in items:
        if not isinstance(it, dict):
            continue
        full = str(it.get("name") or it.get("fullName") or "").strip().lower()
        if full == target:
            best = it
            break

    if best is None:
        best = items[0] if isinstance(items[0], dict) else None
    if not best:
        return ""

    pid = best.get("playerId") or best.get("id") or best.get("player_id")
    return str(pid) if pid else ""


def search_nhl_player(name: str) -> str:
    """Resolve NHL player ID. Returns '' if not found."""
    name = (name or "").strip()
    if not name:
        return ""

    # Suggest first
    pid = _search_suggest(name)
    if pid:
        return pid

    # Fallback
    pid = _search_d3(name)
    if pid:
        return pid

    return ""


def resolve_player_id(name: str) -> str:
    """
    Supports combos like "A + B" -> "idA|idB"
    If one side fails, returns the other.
    """
    name = (name or "").strip()
    if not name:
        return ""

    parts = [p.strip() for p in COMBO_SPLIT_RE.split(name) if p.strip()]
    if len(parts) <= 1:
        return search_nhl_player(name)

    ids = []
    for p in parts:
        pid = search_nhl_player(p)
        if pid:
            ids.append(pid)

    # stable + deterministic
    ids = sorted(set(ids), key=lambda x: int(x) if x.isdigit() else 10**18)
    return "|".join(ids)


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], path: str):
    if not rows:
        print("No rows to write.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"✅ Saved {len(rows)} rows -> {path}")


def fetch_nhl_schedule_home_away(slate_date: str) -> dict:
    """
    Calls the NHL Stats API schedule endpoint for slate_date (YYYY-MM-DD).
    Returns a dict keyed by frozenset({away_abbrev, home_abbrev}) ->
        {"away": abbrev, "home": abbrev}
    so we can look up home/away for any two-team matchup.
    Gracefully returns {} on any failure.
    """
    url = f"https://api-web.nhle.com/v1/schedule/{slate_date}"
    print(f"  Fetching NHL schedule for {slate_date} from {url} ...")
    data = fetch_json(url)
    if not data:
        print("  ⚠️  Schedule fetch failed — is_home will default to 0")
        return {}

    lookup = {}
    game_week = data.get("gameWeek") or []
    for day in game_week:
        if day.get("date") != slate_date:
            continue
        for game in day.get("games") or []:
            away = (game.get("awayTeam") or {}).get("abbrev", "").upper()
            home = (game.get("homeTeam") or {}).get("abbrev", "").upper()
            if away and home:
                key = frozenset({away, home})
                lookup[key] = {"away": away, "home": home}
                print(f"    {away} @ {home}")

    print(f"  ✅ Schedule: {len(lookup)} games found")
    return lookup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="step1_nhl_props.csv")
    parser.add_argument("--output", default="step2_nhl_picktypes.csv")
    parser.add_argument("--cache", default="nhl_id_cache.csv")
    parser.add_argument("--sleep", type=float, default=0.15, help="Delay between uncached lookups (seconds)")
    args = parser.parse_args()

    rows = read_csv(args.input)
    cache = load_cache(args.cache)

    # Derive slate date from the first game_start in the data
    slate_date = None
    for row in rows:
        gs = (row.get("game_start") or "").strip()
        if gs:
            slate_date = gs[:10]
            break
    if not slate_date:
        from datetime import date
        slate_date = date.today().isoformat()

    # Fetch NHL schedule once to resolve home/away
    schedule_lookup = fetch_nhl_schedule_home_away(slate_date)

    new_lookups = 0
    results = []

    for row in _tqdm(rows, desc="  Processing players", unit="player"):
        stat_raw = row.get("stat_type", "") or ""
        stat_norm = STAT_NORM.get(stat_raw, stat_raw.lower().replace(" ", "_"))
        row["stat_norm"] = stat_norm

        # Classify player role
        pos = row.get("position", "") or ""
        if pos in GOALIE_POSITIONS or stat_norm in GOALIE_PROPS:
            row["player_role"] = "GOALIE"
        else:
            row["player_role"] = "SKATER"

        # Determine opponent and home/away
        team = (row.get("team", "") or "").upper()
        away_field = (row.get("away_team", "") or "").upper()
        home_field = (row.get("home_team", "") or "").upper()

        if team and away_field and home_field:
            # PrizePicks provided both — use directly
            if team == away_field:
                row["opponent"] = home_field
                row["is_home"] = "0"
            elif team == home_field:
                row["opponent"] = away_field
                row["is_home"] = "1"
            else:
                row["opponent"] = ""
                row["is_home"] = "0"
        else:
            # PrizePicks description holds the opponent abbreviation
            desc = (row.get("description", "") or "").strip().upper()
            opp = desc if desc else ""
            row["opponent"] = opp

            # Use NHL schedule lookup for authoritative home/away
            if team and opp and schedule_lookup:
                key = frozenset({team, opp})
                game = schedule_lookup.get(key)
                if game:
                    row["away_team"] = game["away"]
                    row["home_team"] = game["home"]
                    row["is_home"] = "1" if team == game["home"] else "0"
                else:
                    row["is_home"] = "0"
            else:
                row["is_home"] = "0"

        # Resolve NHL player ID (cache-first)
        name = (row.get("player_name", "") or "").strip()
        name_key = name.lower()

        if name_key in cache:
            nhl_id = cache[name_key]
        else:
            print(f"  Looking up: {name} ... ", end="", flush=True)
            nhl_id = resolve_player_id(name)
            cache[name_key] = nhl_id  # store even if blank to avoid repeated calls
            new_lookups += 1
            print(nhl_id if nhl_id else "NOT FOUND")
            time.sleep(max(0.0, args.sleep))

        row["nhl_player_id"] = nhl_id
        is_combo = "|" in (nhl_id or "")
        row["combo_prop"] = "True" if is_combo else "False"
        row["combo_player_ids"] = nhl_id if is_combo else ""
        results.append(row)

    if new_lookups > 0:
        save_cache(cache, args.cache)
        print(f"✅ Cache updated: {new_lookups} new lookups saved to {args.cache}")

    write_csv(results, args.output)

    # Quick summaries
    roles = {}
    for r in results:
        roles[r["player_role"]] = roles.get(r["player_role"], 0) + 1
    print(f"Role breakdown: {roles}")

    stat_counts = {}
    for r in results:
        stat_counts[r["stat_norm"]] = stat_counts.get(r["stat_norm"], 0) + 1
    print("Stat types:")
    for st, cnt in sorted(stat_counts.items(), key=lambda x: -x[1]):
        print(f"  {st}: {cnt}")


if __name__ == "__main__":
    main()