#!/usr/bin/env python3
"""
Build FBS national offensive + defensive unit rankings (pass/rush/points).

Uses ESPN team statistics per FBS school (~130 from standings). National ranks only
(conference metadata stored for reference; no conference-relative ranks).

Output: Sports/CFB/data/reference/cfb_team_unit_rankings.csv

  py -3.14 scripts/build_cfb_unit_rankings.py --season 2025
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

_CFB_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

FBS_TEAMS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams"
    "?limit=500&groups=80"
)
STANDINGS_URL = (
    "https://site.api.espn.com/apis/v2/sports/football/college-football/standings"
    "?season={season}&type=0"
)
TEAM_STATS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
    "/teams/{team_id}/statistics"
)

REQUEST_DELAY_S = 0.25
MAX_RETRIES = 5

OUTPUT_COLS = [
    "team_id",
    "team_abbr",
    "team_name",
    "conference_id",
    "conference_name",
    "season",
    "off_pass_ypg",
    "off_pass_rank",
    "off_rush_ypg",
    "off_rush_rank",
    "off_points_pg",
    "off_points_rank",
    "def_pass_ypg_allowed",
    "def_pass_rank",
    "def_rush_ypg_allowed",
    "def_rush_rank",
    "def_points_allowed_pg",
    "def_points_rank",
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


def fetch_fbs_teams_from_standings(session: requests.Session, season: int) -> list[dict[str, str]]:
    """
    FBS team list from conference standings (~130 teams).
    ESPN groups=80 on the teams endpoint still returns hundreds of schools; standings
  is the reliable FBS filter.
    """
    j = get_json(session, STANDINGS_URL.format(season=season))
    by_id: dict[str, dict[str, str]] = {}
    for child in j.get("children") or []:
        if not isinstance(child, dict):
            continue
        conf_id = str(child.get("id") or "").strip()
        conf_name = str(child.get("name") or "").strip()
        entries = (child.get("standings") or {}).get("entries") or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            team = entry.get("team") or {}
            tid = str(team.get("id") or "").strip()
            if not tid:
                continue
            by_id[tid] = {
                "team_id": tid,
                "team_abbr": str(team.get("abbreviation") or "").strip().upper(),
                "team_name": str(team.get("displayName") or team.get("name") or "").strip(),
                "conference_id": conf_id,
                "conference_name": conf_name,
            }
    return list(by_id.values())


def _stat_value(blocks: list[dict[str, Any]], category: str, stat_names: tuple[str, ...]) -> float | None:
    for block in blocks:
        bname = str(block.get("name") or "").lower()
        bdisp = str(block.get("displayName") or "").lower()
        if category.lower() not in (bname, bdisp):
            continue
        for st in block.get("stats") or []:
            if str(st.get("name") or "") in stat_names:
                try:
                    return float(st.get("value"))
                except (TypeError, ValueError):
                    return None
    return None


def parse_team_unit_stats(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") or {}
    season = int(
        (results.get("requestedSeason") or {}).get("year")
        or (results.get("season") or {}).get("year")
        or 0
    )
    own = (results.get("stats") or {}).get("categories") or []
    opp = results.get("opponent") or []

    off_pass = _stat_value(
        own,
        "passing",
        ("netPassingYardsPerGame", "passingYardsPerGame", "yardsPerGame"),
    )
    off_rush = _stat_value(own, "rushing", ("rushingYardsPerGame", "yardsPerGame"))
    off_pts = _stat_value(own, "scoring", ("totalPointsPerGame",))

    def_pass = _stat_value(
        opp,
        "passing",
        ("netPassingYardsPerGame", "passingYardsPerGame", "yardsPerGame"),
    )
    def_rush = _stat_value(opp, "rushing", ("rushingYardsPerGame", "yardsPerGame"))
    def_pts = _stat_value(opp, "scoring", ("totalPointsPerGame",))

    return {
        "season": season,
        "off_pass_ypg": off_pass,
        "off_rush_ypg": off_rush,
        "off_points_pg": off_pts,
        "def_pass_ypg_allowed": def_pass,
        "def_rush_ypg_allowed": def_rush,
        "def_points_allowed_pg": def_pts,
    }


def _has_stats(row: dict[str, Any]) -> bool:
    keys = (
        "off_pass_ypg",
        "off_rush_ypg",
        "off_points_pg",
        "def_pass_ypg_allowed",
        "def_rush_ypg_allowed",
        "def_points_allowed_pg",
    )
    return any(row.get(k) is not None and pd.notna(row.get(k)) for k in keys)


def resolve_season(session: requests.Session, teams: list[dict[str, str]], hint: int) -> int:
    """Pick season year with populated team stats (off-season fallback)."""
    candidates: list[int] = []
    if hint:
        candidates.append(hint)
    y = datetime.date.today().year
    candidates.extend([y, y - 1, y - 2])
    seen: set[int] = set()
    sample = teams[: min(8, len(teams))]
    for yr in candidates:
        if yr in seen:
            continue
        seen.add(yr)
        if not sample:
            continue
        filled = 0
        for t in sample:
            url = TEAM_STATS_URL.format(team_id=t["team_id"])
            try:
                payload = get_json(session, url)
                parsed = parse_team_unit_stats(payload)
                if int(parsed.get("season") or yr) == yr and _has_stats(parsed):
                    filled += 1
            except Exception:
                pass
            time.sleep(REQUEST_DELAY_S)
        if filled >= max(2, len(sample) // 2):
            return yr
    return hint or (y - 1)


def pull_all_team_stats(
    session: requests.Session,
    teams: list[dict[str, str]],
    season: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for team in teams:
        name = team.get("team_name") or team.get("team_abbr") or team.get("team_id")
        print(f"CFB rankings: fetching {name}...")
        url = TEAM_STATS_URL.format(team_id=team["team_id"])
        payload = get_json(session, url)
        time.sleep(REQUEST_DELAY_S)
        parsed = parse_team_unit_stats(payload)
        row = {**team, **parsed, "season": season}
        rows.append(row)
    return rows


def _rank_series(values: pd.Series, *, ascending: bool) -> pd.Series:
    return values.rank(method="min", ascending=ascending).astype("Int64")


def add_national_ranks(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["off_pass_rank"] = _rank_series(
        pd.to_numeric(out["off_pass_ypg"], errors="coerce"), ascending=False
    )
    out["off_rush_rank"] = _rank_series(
        pd.to_numeric(out["off_rush_ypg"], errors="coerce"), ascending=False
    )
    out["off_points_rank"] = _rank_series(
        pd.to_numeric(out["off_points_pg"], errors="coerce"), ascending=False
    )
    out["def_pass_rank"] = _rank_series(
        pd.to_numeric(out["def_pass_ypg_allowed"], errors="coerce"), ascending=True
    )
    out["def_rush_rank"] = _rank_series(
        pd.to_numeric(out["def_rush_ypg_allowed"], errors="coerce"), ascending=True
    )
    out["def_points_rank"] = _rank_series(
        pd.to_numeric(out["def_points_allowed_pg"], errors="coerce"), ascending=True
    )
    return out


def build_rankings_table(session: requests.Session, season_hint: int) -> tuple[pd.DataFrame, int]:
    teams = fetch_fbs_teams_from_standings(session, season_hint)
    if not teams:
        raise RuntimeError(f"No FBS teams from standings for season {season_hint}")

    season = resolve_season(session, teams, season_hint)
    if season != season_hint:
        print(f"[CFB rankings] Using season {season} (stats empty for {season_hint})")
        teams = fetch_fbs_teams_from_standings(session, season)
        if not teams:
            raise RuntimeError(f"No FBS teams from standings for season {season}")

    rows = pull_all_team_stats(session, teams, season)
    df = pd.DataFrame(rows)
    if df.empty:
        return df, season

    with_stats = df[df.apply(_has_stats, axis=1)]
    if len(with_stats) < max(20, len(df) * 0.25):
        print(
            f"[CFB rankings] WARN: only {len(with_stats)}/{len(df)} teams with stats "
            f"for season {season}"
        )

    df = add_national_ranks(df)
    df["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for col in OUTPUT_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[OUTPUT_COLS].sort_values(["conference_name", "team_abbr"]).reset_index(drop=True), season


def main() -> None:
    ap = argparse.ArgumentParser(description="Build FBS national CFB unit rankings CSV.")
    ap.add_argument("--season", type=int, default=0, help="ESPN season year (0 = auto).")
    ap.add_argument(
        "--out",
        default="",
        help="Output CSV (default: data/reference/cfb_team_unit_rankings.csv).",
    )
    args = ap.parse_args()

    out_path = (
        Path(args.out)
        if args.out
        else _CFB_ROOT / "data" / "reference" / "cfb_team_unit_rankings.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    season_hint = int(args.season) if args.season else datetime.date.today().year
    session = requests.Session()

    print(f"→ CFB FBS unit rankings (national) | season hint={season_hint}")
    df, season = build_rankings_table(session, season_hint)
    if df.empty:
        print("❌ No FBS teams ranked — check season / network.")
        raise SystemExit(1)

    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    updated = df["updated_at"].iloc[0] if len(df) else ""
    print(f"✅ Wrote {len(df)} teams → {out_path}")
    print(f"   season:    {season}")
    print(f"   updated:   {updated}")
    print(f"   conferences: {df['conference_name'].nunique()}")


if __name__ == "__main__":
    main()
