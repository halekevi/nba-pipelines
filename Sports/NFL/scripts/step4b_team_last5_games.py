#!/usr/bin/env python3
"""
NFL step4b — last N completed regular-season games per team (ESPN scoreboards).

Builds rolling team form for matchup context: points for/against, record, margin
over the team's most recent N games (default N=5).

Output: NFL/data/nfl_team_last5.csv (one row per team abbreviation).

  set NFL_PIPELINE_ACTIVE=1
  py -3.14 scripts/step4b_team_last5_games.py --season 2025

Run from Sports/NFL/ (or pass absolute --output).
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# PrizePicks / alternate abbreviations → ESPN scoreboard abbreviations
_ABBR_ALIAS = {
    "LA": "LAR",  # Rams often listed as LA on books
    "WAS": "WSH",
    "JAC": "JAX",
}


def _abbr(raw: str) -> str:
    a = str(raw or "").strip().upper()
    return _ABBR_ALIAS.get(a, a)


def _parse_event(ev: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Return two team rows for one completed game, or None."""
    comps = ev.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    st = (comp.get("status") or {}).get("type") or {}
    if not st.get("completed"):
        return None
    date_s = str(ev.get("date") or "").strip()
    try:
        game_dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
    except ValueError:
        game_dt = None

    teams: list[dict[str, Any]] = []
    for c in comp.get("competitors") or []:
        tm = c.get("team") or {}
        ab = _abbr(tm.get("abbreviation") or "")
        if not ab:
            return None
        try:
            sc = int(float(c.get("score") or 0))
        except (TypeError, ValueError):
            sc = 0
        teams.append({"abbr": ab, "score": sc, "home": str(c.get("homeAway") or "").lower() == "home"})

    if len(teams) != 2:
        return None
    a, b = teams[0], teams[1]
    out: list[dict[str, Any]] = []
    for side, other in ((a, b), (b, a)):
        pf, pa = int(side["score"]), int(other["score"])
        if pf > pa:
            res = "W"
        elif pf < pa:
            res = "L"
        else:
            res = "T"
        out.append(
            {
                "team": side["abbr"],
                "game_date": game_dt,
                "date_iso": date_s[:10] if len(date_s) >= 10 else date_s,
                "opp": other["abbr"],
                "pf": pf,
                "pa": pa,
                "result": res,
            }
        )
    return out


def fetch_regular_season_games(season: int, *, max_week: int, timeout: float, sleep_s: float) -> list[dict[str, Any]]:
    import time
    import random

    all_rows: list[dict[str, Any]] = []
    for week in range(1, max_week + 1):
        url = (
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
            f"?season={season}&seasontype=2&week={week}"
        )
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        j = r.json()
        for ev in j.get("events") or []:
            rows = _parse_event(ev)
            if rows:
                all_rows.extend(rows)
        time.sleep(max(0.0, sleep_s) + random.uniform(0, 0.15))
    return all_rows


def aggregate_last_n(team_games: list[dict[str, Any]], n: int) -> dict[str, Any]:
    """team_games sorted oldest-first; use last n completed."""
    if not team_games:
        return {
            "last5_n": 0,
            "last5_w": 0,
            "last5_l": 0,
            "last5_t": 0,
            "last5_pf_pg": None,
            "last5_pa_pg": None,
            "last5_margin_avg": None,
            "last5_results": "",
            "last5_opps": "",
        }
    g = team_games[-n:] if len(team_games) >= n else team_games[:]
    w = sum(1 for x in g if x["result"] == "W")
    l = sum(1 for x in g if x["result"] == "L")
    t = sum(1 for x in g if x["result"] == "T")
    pf = sum(x["pf"] for x in g)
    pa = sum(x["pa"] for x in g)
    nn = len(g)
    # Most recent first for string (g is chronological; reverse for display)
    rev = list(reversed(g))
    return {
        "last5_n": nn,
        "last5_w": w,
        "last5_l": l,
        "last5_t": t,
        "last5_pf_pg": round(pf / nn, 2) if nn else None,
        "last5_pa_pg": round(pa / nn, 2) if nn else None,
        "last5_margin_avg": round((pf - pa) / nn, 2) if nn else None,
        "last5_results": ",".join(x["result"] for x in rev),
        "last5_opps": ",".join(x["opp"] for x in rev),
    }


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser(description="Fetch each NFL team's last N completed regular-season games (ESPN).")
    ap.add_argument("--season", type=int, required=True, help="League season year (e.g. 2025 for 2025 NFL season).")
    ap.add_argument("--n-games", type=int, default=5, dest="n_games", help="Rolling window size (default 5).")
    ap.add_argument("--max-week", type=int, default=18, help="Regular season weeks to scan (default 18).")
    ap.add_argument("--output", default="data/nfl_team_last5.csv")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--sleep", type=float, default=0.25, help="Delay between week requests.")
    args = ap.parse_args()

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parents[1] / out_path

    print(
        f"[NFL step4b] Fetching {args.max_week} regular-season weeks for season={args.season} "
        f"(last {args.n_games} games per team)..."
    )
    rows = fetch_regular_season_games(
        int(args.season), max_week=int(args.max_week), timeout=float(args.timeout), sleep_s=float(args.sleep)
    )
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_team[r["team"]].append(r)

    for ab in by_team:
        by_team[ab].sort(key=lambda x: (x["game_date"] or datetime.min, x["date_iso"]))

    records: list[dict[str, Any]] = []
    for team in sorted(by_team.keys()):
        agg = aggregate_last_n(by_team[team], int(args.n_games))
        rec = {"team": team, **agg}
        rec["last5_record"] = f"{agg['last5_w']}-{agg['last5_l']}" + (f"-{agg['last5_t']}" if agg["last5_t"] else "")
        records.append(rec)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame.from_records(records).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[NFL step4b] Wrote {out_path} rows={len(records)} (season={args.season}, n={args.n_games})")


if __name__ == "__main__":
    main()
