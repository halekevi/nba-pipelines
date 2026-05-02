#!/usr/bin/env python3
"""
fetch_cbb_actuals_by_date.py

Fetch CBB actual player stats from ESPN Site API for a given date (or inferred from a slate file),
and write a flat actuals CSV keyed by espn_athlete_id.

Inputs:
- --date YYYY-MM-DD (optional) OR infer from --slate start_time
- --slate step5b_cbb.csv (optional; used for date inference and optional athlete filtering)
- --out output csv path (required)
- --all_players (default True): include all players found in boxscores
- --only_slate_players: only output athletes found in the slate file (faster/smaller)
- --sleep seconds between ESPN calls (default 0.25)

Output columns (per athlete aggregated per event):
- date, event_id, team_abbr, opponent_abbr, espn_athlete_id, player_name,
  MIN, PTS, REB, AST, STL, BLK, TO, FG, FGA, 3PT, 3PTA, FT, FTA

One-line examples:
  py -3.14 .\fetch_cbb_actuals_by_date.py --slate step5b_cbb.csv --out cbb_actuals.csv
  py -3.14 .\fetch_cbb_actuals_by_date.py --date 2026-02-21 --out cbb_actuals_2026-02-21.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={yyyymmdd}"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={event_id}"


def http_get_json(url: str, timeout: int = 30, retries: int = 4, backoff: float = 0.8) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Failed GET {url} after {retries} retries. Last error: {last_err}")


def infer_date_from_slate(slate_path: str) -> str:
    import pandas as pd  # local import
    df = pd.read_csv(slate_path, dtype=str).fillna("")
    if "start_time" not in df.columns:
        raise RuntimeError("Slate file missing start_time; pass --date explicitly.")
    st = df.loc[df["start_time"].astype(str).str.strip() != "", "start_time"].astype(str)
    if st.empty:
        raise RuntimeError("Slate file has no start_time values; pass --date explicitly.")
    # Example: 2026-02-21T20:30:00.000-05:00
    s0 = st.iloc[0]
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s0)
    if not m:
        raise RuntimeError(f"Could not parse date from start_time='{s0}'. Pass --date explicitly.")
    return m.group(1)


def yyyymmdd(date_yyyy_mm_dd: str) -> str:
    dt = datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d")
    return dt.strftime("%Y%m%d")


def parse_team_abbr(comp: Dict[str, Any]) -> str:
    # ESPN uses 'abbreviation' in competitor.team
    t = comp.get("team", {}) if isinstance(comp, dict) else {}
    ab = t.get("abbreviation") or t.get("shortDisplayName") or t.get("displayName") or ""
    return str(ab).strip()


def extract_events(scoreboard: Dict[str, Any]) -> List[str]:
    events = scoreboard.get("events", [])
    ids: List[str] = []
    if isinstance(events, list):
        for e in events:
            eid = str(e.get("id", "")).strip()
            if eid:
                ids.append(eid)
    return ids


def safe_int(x: Any) -> Optional[int]:
    try:
        s = str(x).strip()
        if s == "" or s.lower() == "none":
            return None
        return int(float(s))
    except Exception:
        return None


def safe_float(x: Any) -> Optional[float]:
    try:
        s = str(x).strip()
        if s == "" or s.lower() == "none":
            return None
        return float(s)
    except Exception:
        return None


def parse_minutes(min_str: Any) -> Optional[float]:
    # ESPN sometimes provides "MM:SS" or "MM"
    s = str(min_str).strip()
    if not s:
        return None
    if ":" in s:
        mm, ss = s.split(":", 1)
        try:
            return float(mm) + float(ss) / 60.0
        except Exception:
            return None
    return safe_float(s)


STAT_KEYS = {
    "MIN": ["MIN", "MINUTES"],
    "PTS": ["PTS", "POINTS"],
    "REB": ["REB", "REBOUNDS", "TRB"],
    "AST": ["AST", "ASSISTS"],
    "STL": ["STL", "STEALS"],
    "BLK": ["BLK", "BLOCKS"],
    "TO":  ["TO", "TOV", "TURNOVERS"],
    "FG":  ["FG", "FGM"],
    "FGA": ["FGA"],
    "3PT": ["3PT", "3PM", "FG3M"],
    "3PTA":["3PTA", "3PA", "FG3A"],
    "FT":  ["FT", "FTM"],
    "FTA": ["FTA"],
}


def normalize_header(h: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(h).upper()).strip()


def extract_player_rows_from_boxscore(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ESPN summary -> boxscore -> players -> statistics.
    We try to robustly pull all athletes in the game with stat lines.
    """
    box = summary.get("boxscore", {})
    players_blocks = box.get("players", [])
    out: List[Dict[str, Any]] = []

    if not isinstance(players_blocks, list):
        return out

    for team_block in players_blocks:
        team = team_block.get("team", {}) if isinstance(team_block, dict) else {}
        team_abbr = str(team.get("abbreviation", "")).strip()
        opp_abbr = ""  # filled later if desired

        stats_list = team_block.get("statistics", [])
        if not isinstance(stats_list, list):
            continue

        for stat_block in stats_list:
            # "name": "starters"/"bench", "labels": [...], "athletes": [...]
            labels = stat_block.get("labels") or stat_block.get("keys") or []
            athletes = stat_block.get("athletes") or []
            if not isinstance(labels, list) or not isinstance(athletes, list):
                continue

            # Build header index map
            header_map: Dict[str, int] = {}
            norm_labels = [normalize_header(x) for x in labels]
            for i, lab in enumerate(norm_labels):
                header_map[lab] = i

            for a in athletes:
                athlete = a.get("athlete", {}) if isinstance(a, dict) else {}
                aid = str(athlete.get("id", "")).strip()
                name = str(athlete.get("displayName", athlete.get("fullName", ""))).strip()
                if not aid:
                    continue

                stats = a.get("stats") or a.get("statistics") or []
                if not isinstance(stats, list):
                    stats = []

                def get_stat(key: str) -> Optional[float]:
                    for alias in STAT_KEYS.get(key, []):
                        idx = header_map.get(normalize_header(alias))
                        if idx is not None and idx < len(stats):
                            if key == "MIN":
                                return parse_minutes(stats[idx])
                            return safe_float(stats[idx])
                    return None

                row = {
                    "team_abbr": team_abbr,
                    "opponent_abbr": opp_abbr,
                    "espn_athlete_id": aid,
                    "player_name": name,
                    "MIN": get_stat("MIN"),
                    "PTS": get_stat("PTS"),
                    "REB": get_stat("REB"),
                    "AST": get_stat("AST"),
                    "STL": get_stat("STL"),
                    "BLK": get_stat("BLK"),
                    "TO":  get_stat("TO"),
                    "FG":  get_stat("FG"),
                    "FGA": get_stat("FGA"),
                    "3PT": get_stat("3PT"),
                    "3PTA": get_stat("3PTA"),
                    "FT":  get_stat("FT"),
                    "FTA": get_stat("FTA"),
                }
                out.append(row)

    return out


def maybe_filter_to_slate_players(rows: List[Dict[str, Any]], slate_ids: Set[str]) -> List[Dict[str, Any]]:
    if not slate_ids:
        return rows
    return [r for r in rows if str(r.get("espn_athlete_id", "")).strip() in slate_ids]


def read_slate_ids(slate_path: str) -> Set[str]:
    import pandas as pd  # local import
    df = pd.read_csv(slate_path, dtype=str).fillna("")
    if "espn_athlete_id" not in df.columns:
        return set()
    ids = set(df["espn_athlete_id"].astype(str).str.strip())
    ids.discard("")
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="YYYY-MM-DD (optional; inferred from --slate if omitted)")
    ap.add_argument("--slate", default="", help="Slate CSV (optional; used to infer date and optionally filter athletes)")
    ap.add_argument("--out", required=True, help="Output CSV path")
    ap.add_argument("--only_slate_players", action="store_true", help="Only output athletes found in slate file")
    ap.add_argument("--sleep", type=float, default=0.25, help="Seconds to sleep between ESPN calls")
    args = ap.parse_args()

    date_str = args.date.strip()
    if not date_str:
        if not args.slate:
            raise RuntimeError("Provide --date YYYY-MM-DD or --slate <file> to infer date.")
        date_str = infer_date_from_slate(args.slate)

    d8 = yyyymmdd(date_str)
    scoreboard = http_get_json(SCOREBOARD_URL.format(yyyymmdd=d8))
    event_ids = extract_events(scoreboard)
    if not event_ids:
        raise RuntimeError(f"No ESPN events found for date {date_str} (dates={d8}).")

    slate_ids: Set[str] = set()
    if args.only_slate_players and args.slate:
        slate_ids = read_slate_ids(args.slate)

    rows_out: List[Dict[str, Any]] = []
    for i, eid in enumerate(event_ids, start=1):
        summary = http_get_json(SUMMARY_URL.format(event_id=eid))
        player_rows = extract_player_rows_from_boxscore(summary)
        if args.only_slate_players and slate_ids:
            player_rows = maybe_filter_to_slate_players(player_rows, slate_ids)

        for r in player_rows:
            r["date"] = date_str
            r["event_id"] = eid
            rows_out.append(r)

        if i % 10 == 0 or i == len(event_ids):
            print(f"[{i}/{len(event_ids)}] events processed | rows so far: {len(rows_out)}")
        time.sleep(max(0.0, args.sleep))

    # De-dupe by (date, event_id, athlete_id, team_abbr) keeping last
    dedup: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for r in rows_out:
        key = (r.get("date",""), r.get("event_id",""), str(r.get("espn_athlete_id","")), str(r.get("team_abbr","")))
        dedup[key] = r
    final_rows = list(dedup.values())

    fieldnames = ["date","event_id","team_abbr","opponent_abbr","espn_athlete_id","player_name",
                  "MIN","PTS","REB","AST","STL","BLK","TO","FG","FGA","3PT","3PTA","FT","FTA"]

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in final_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    print(f"✅ Wrote actuals: {args.out} | rows: {len(final_rows)} | events: {len(event_ids)} | date: {date_str}")


if __name__ == "__main__":
    main()
