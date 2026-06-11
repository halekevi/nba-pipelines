#!/usr/bin/env python3
"""
Fetch NFL or CFB player stat actuals from ESPN box scores for slate grading.

  py -3.14 scripts/fetch_football_actuals.py --league nfl --date 2025-09-07
  py -3.14 scripts/fetch_football_actuals.py --league cfb --date 2025-11-15
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

LEAGUE_PATH = {
    "nfl": "football/nfl",
    "cfb": "football/college-football",
}

# ESPN stat group label -> (prop_type, stat label in group)
_STAT_ROWS: list[tuple[str, str, str]] = [
    ("passing", "YDS", "passing_yards"),
    ("passing", "TD", "passing_tds"),
    ("passing", "C", "completions"),
    ("passing", "INT", "interceptions"),
    ("rushing", "YDS", "rushing_yards"),
    ("rushing", "TD", "rushing_tds"),
    ("rushing", "CAR", "rushing_attempts"),
    ("receiving", "YDS", "receiving_yards"),
    ("receiving", "REC", "receptions"),
    ("receiving", "TD", "receiving_tds"),
    ("receiving", "TGTS", "targets"),
    ("defensive", "TOT", "tackles_assists"),
    ("defensive", "SACK", "sacks"),
    ("defensive", "INT", "defensive_interceptions"),
    ("kicking", "PTS", "kicking_points"),
]


def _num(x) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s or s in ("-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None


def _scoreboard_url(league_path: str, date_str: str) -> str:
    d = date_str.replace("-", "")
    return f"https://site.api.espn.com/apis/site/v2/sports/{league_path}/scoreboard?dates={d}"


def _summary_url(league_path: str, event_id: str) -> str:
    return f"https://site.api.espn.com/apis/site/v2/sports/{league_path}/summary?event={event_id}"


def _parse_box(summary: dict, team_abbr: str) -> list[dict]:
    rows: list[dict] = []
    box = summary.get("boxscore") or {}
    for team_block in box.get("players") or []:
        team = str((team_block.get("team") or {}).get("abbreviation") or "").strip().upper()
        if not team:
            continue
        for group in team_block.get("statistics") or []:
            cat = str(group.get("name") or group.get("displayName") or "").strip().lower()
            labels = [str(x).strip().upper() for x in (group.get("labels") or [])]
            for athlete in group.get("athletes") or []:
                ath = athlete.get("athlete") or {}
                name = str(ath.get("displayName") or ath.get("shortName") or "").strip()
                if not name:
                    continue
                stats = athlete.get("stats") or []
                stat_map = {labels[i]: stats[i] for i in range(min(len(labels), len(stats)))}
                for gcat, label, prop_type in _STAT_ROWS:
                    if gcat not in cat:
                        continue
                    val = _num(stat_map.get(label.upper()))
                    if val is None:
                        continue
                    rows.append(
                        {
                            "player": name,
                            "team": team,
                            "prop_type": prop_type,
                            "actual": val,
                        }
                    )
    return rows


def fetch_football_actuals(league: str, date_str: str) -> pd.DataFrame:
    league = league.lower().strip()
    if league not in LEAGUE_PATH:
        raise ValueError(f"Unknown league: {league}")
    path = LEAGUE_PATH[league]
    print(f"\n=== {league.upper()} actuals for {date_str} ===\n")
    url = _scoreboard_url(path, date_str)
    print(f"  Scoreboard: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        events = (r.json() or {}).get("events") or []
    except Exception as exc:
        print(f"  ERROR scoreboard: {exc}")
        return pd.DataFrame(columns=["player", "team", "prop_type", "actual"])

    print(f"  Events: {len(events)}")
    all_rows: list[dict] = []
    for event in events:
        state = ((event.get("status") or {}).get("type") or {}).get("state", "")
        completed = ((event.get("status") or {}).get("type") or {}).get("completed", False)
        if state != "post" and not completed:
            print(f"  Skip {event.get('shortName', '')} — not final")
            continue
        eid = str(event.get("id") or "")
        if not eid:
            continue
        print(f"  Grading: {event.get('shortName', eid)}")
        try:
            sr = requests.get(_summary_url(path, eid), headers=HEADERS, timeout=25)
            sr.raise_for_status()
            all_rows.extend(_parse_box(sr.json(), ""))
            time.sleep(0.2)
        except Exception as exc:
            print(f"    ERROR: {exc}")

    if not all_rows:
        return pd.DataFrame(columns=["player", "team", "prop_type", "actual"])
    df = pd.DataFrame(all_rows)
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    return df.dropna(subset=["actual"]).drop_duplicates(
        subset=["player", "team", "prop_type"], keep="last"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True, choices=["nfl", "cfb"])
    ap.add_argument("--date", default="", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--output", default="")
    args = ap.parse_args()

    if not args.date:
        args.date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    if not args.output:
        args.output = str(_REPO / "outputs" / args.date / f"actuals_{args.league}_{args.date}.csv")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = fetch_football_actuals(args.league, args.date)
    if df.empty:
        pd.DataFrame(columns=["player", "team", "prop_type", "actual"]).to_csv(out, index=False)
        print(f"\nNo actuals yet — wrote empty stub -> {out}")
        return
    df.to_csv(out, index=False)
    print(f"\nSaved -> {out}  ({len(df)} rows)")
    print(f"Prop types: {sorted(df['prop_type'].unique().tolist())}")


if __name__ == "__main__":
    main()
