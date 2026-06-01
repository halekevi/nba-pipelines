#!/usr/bin/env python3
"""
Standalone NFL team defensive reference pull (ESPN unofficial API).

Not tied to a slate date. CFB defense: use Sports/CFB/scripts/build_cfb_unit_rankings.py.

Output: data/reference/nfl_team_defense.csv
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_PATH = REPO_ROOT / "data" / "reference" / "nfl_team_defense.csv"
ROSTER_PATH = REPO_ROOT / "data" / "rosters" / "nfl_rosters.csv"
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=50"
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
TEAM_STATS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/statistics"
)

REQUEST_DELAY_S = 0.25
MAX_RETRIES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

OUTPUT_COLS = [
    "team_id",
    "team_abbr",
    "team_name",
    "season",
    "points_allowed_pg",
    "pa_rank",
    "opp_pass_ypg",
    "pass_def_rank",
    "opp_rush_ypg",
    "rush_def_rank",
    "sacks",
    "sacks_rank",
    "turnovers_forced",
    "to_rank",
    "updated_at",
]


def get_json(session: requests.Session, url: str) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 429:
                print(f"  [429] retry in 1s ({url[:72]}...)", file=sys.stderr)
                time.sleep(1.0)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"GET failed: {url}") from last_err


def load_teams(session: requests.Session, roster_path: Path) -> list[dict[str, str]]:
    if roster_path.is_file():
        df = pd.read_csv(roster_path, dtype=str).fillna("")
        teams = (
            df.drop_duplicates(subset=["team_id"])
            .loc[:, ["team_id", "team_abbr", "team_name"]]
            .astype(str)
            .to_dict(orient="records")
        )
        if teams:
            return teams

    payload = get_json(session, TEAMS_URL)
    raw = payload.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
    out: list[dict[str, str]] = []
    for item in raw:
        t = item.get("team", item) if isinstance(item, dict) else {}
        tid = str(t.get("id", "")).strip()
        if tid:
            out.append(
                {
                    "team_id": tid,
                    "team_abbr": str(t.get("abbreviation", "")).strip().upper(),
                    "team_name": str(t.get("displayName", "")).strip(),
                }
            )
    return out


def resolve_season(session: requests.Session, hint: int) -> int:
    """Pick latest NFL season year with standings data (handles off-season)."""
    candidates: list[int] = []
    if hint:
        candidates.append(hint)
    y = datetime.date.today().year
    candidates.extend([y, y - 1, y - 2])
    seen: set[int] = set()
    for yr in candidates:
        if yr in seen:
            continue
        seen.add(yr)
        if standings_points_against(session, yr):
            return yr
    return hint or (y - 1)


def standings_points_against(session: requests.Session, season: int) -> dict[str, dict[str, float]]:
    url = f"{STANDINGS_URL}?season={season}&type=0"
    j = get_json(session, url)
    out: dict[str, dict[str, float]] = {}
    for child in j.get("children") or []:
        for entry in (child.get("standings") or {}).get("entries") or []:
            team = entry.get("team") or {}
            abbr = str(team.get("abbreviation") or "").strip().upper()
            if not abbr:
                continue
            games = 0.0
            pa = 0.0
            for st in entry.get("stats") or []:
                name = str(st.get("name") or "")
                if name in ("wins", "losses", "ties"):
                    games += float(st.get("value") or 0)
                elif name == "pointsAgainst":
                    pa = float(st.get("value") or 0)
            out[abbr] = {"points_against": pa, "games": max(games, 1.0)}
    return out


def _stat_value(blocks: list[dict[str, Any]], category: str, stat_name: str) -> float | None:
    for block in blocks:
        if str(block.get("name") or "").lower() != category.lower():
            if str(block.get("displayName") or "").lower() != category.lower():
                continue
        for st in block.get("stats") or []:
            if str(st.get("name") or "") == stat_name:
                try:
                    return float(st.get("value"))
                except (TypeError, ValueError):
                    return None
    return None


def parse_team_defense_stats(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") or {}
    season = int(
        (results.get("requestedSeason") or {}).get("year")
        or (results.get("season") or {}).get("year")
        or datetime.date.today().year
    )
    opp = results.get("opponent") or []
    own = (results.get("stats") or {}).get("categories") or []

    opp_pass_ypg = _stat_value(opp, "passing", "netPassingYardsPerGame")
    if opp_pass_ypg is None:
        opp_pass_ypg = _stat_value(opp, "passing", "passingYardsPerGame")

    opp_rush_ypg = _stat_value(opp, "rushing", "rushingYardsPerGame")
    turnovers = _stat_value(opp, "miscellaneous", "totalTakeaways")
    sacks = _stat_value(own, "defensive", "sacks")

    return {
        "season": season,
        "opp_pass_ypg": opp_pass_ypg,
        "opp_rush_ypg": opp_rush_ypg,
        "sacks": sacks,
        "turnovers_forced": turnovers,
    }


def _rank_series(values: pd.Series, *, ascending: bool) -> pd.Series:
    return values.rank(method="min", ascending=ascending).astype("Int64")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull NFL team defense reference stats.")
    ap.add_argument("--season", type=int, default=0, help="NFL season year (0 = from API).")
    ap.add_argument("--output", default=str(OUTPUT_PATH))
    ap.add_argument("--roster", default=str(ROSTER_PATH))
    args = ap.parse_args()

    session = requests.Session()
    teams = load_teams(session, Path(args.roster))
    if not teams:
        print("[NFL defense] ERROR: no teams found", file=sys.stderr)
        return 1

    season_hint = int(args.season) if args.season else 0
    season = resolve_season(session, season_hint)
    if season_hint and season_hint != season:
        print(f"[NFL defense] Using season {season} (standings empty for {season_hint})")
    pa_map = standings_points_against(session, season)
    time.sleep(REQUEST_DELAY_S)

    rows: list[dict[str, Any]] = []
    for team in teams:
        tid = team["team_id"]
        abbr = str(team["team_abbr"]).upper()
        name = team["team_name"]
        print(f"Fetching defense: {name}...")
        url = TEAM_STATS_URL.format(team_id=tid)
        payload = get_json(session, url)
        time.sleep(REQUEST_DELAY_S)

        parsed = parse_team_defense_stats(payload)
        row_season = int(args.season) if args.season else season
        g = pa_map.get(abbr, {}).get("games", 17.0)
        pa_total = pa_map.get(abbr, {}).get("points_against", float("nan"))
        pa_pg = float(pa_total) / float(g) if g and pa_total == pa_total else float("nan")

        rows.append(
            {
                "team_id": tid,
                "team_abbr": abbr,
                "team_name": name,
                "season": row_season,
                "points_allowed_pg": pa_pg,
                "opp_pass_ypg": parsed["opp_pass_ypg"],
                "opp_rush_ypg": parsed["opp_rush_ypg"],
                "sacks": parsed["sacks"],
                "turnovers_forced": parsed["turnovers_forced"],
            }
        )

    df = pd.DataFrame(rows)
    df["pa_rank"] = _rank_series(df["points_allowed_pg"], ascending=True)
    df["pass_def_rank"] = _rank_series(df["opp_pass_ypg"], ascending=True)
    df["rush_def_rank"] = _rank_series(df["opp_rush_ypg"], ascending=True)
    df["sacks_rank"] = _rank_series(df["sacks"], ascending=False)
    df["to_rank"] = _rank_series(df["turnovers_forced"], ascending=False)
    df["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df[OUTPUT_COLS].to_csv(out, index=False, encoding="utf-8")
    season_out = int(df["season"].iloc[0]) if len(df) else season
    print(f"\n=== Summary ===")
    print(f"  season:       {season_out}")
    print(f"  teams pulled: {len(df)}")
    print(f"  updated_at:   {df['updated_at'].iloc[0] if len(df) else ''}")
    print(f"  wrote:        {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
