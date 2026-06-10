#!/usr/bin/env python3
"""
Pull complete NFL and CFB team rosters from ESPN's public site API (no auth).

Outputs CSV + JSON under data/rosters/.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
ROSTER_DIR = REPO_ROOT / "data" / "rosters"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_S = 0.25
MAX_RETRIES = 5
CFB_TEAMS_PAGE_LIMIT = 500

NFL_TEAMS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=50"
)
NFL_ROSTER_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
)

CFB_TEAMS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams"
)
CFB_TEAM_ROSTER_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{team_id}"
    "?enable=roster"
)
CFB_STANDINGS_URL = (
    "https://site.api.espn.com/apis/v2/sports/football/college-football/standings"
)

ROW_FIELDS = (
    "team_id",
    "team_abbr",
    "team_name",
    "conference_id",
    "conference_name",
    "sport",
    "player_id",
    "player_name",
    "position",
    "jersey",
    "status",
)

SportKey = str  # "nfl" | "cfb"


@dataclass
class TeamInfo:
    team_id: str
    team_abbr: str
    team_name: str
    conference_id: str = ""
    conference_name: str = ""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
    )
    return s


def get_json(session: requests.Session, url: str) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 429:
                print(f"  [429] rate limited, retrying in 1s ({url[:80]}...)", file=sys.stderr)
                time.sleep(1.0)
                continue
            if resp.status_code == 403:
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} tries: {url}") from last_err


def _league_from_teams_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return payload["sports"][0]["leagues"][0]
    except (IndexError, KeyError, TypeError):
        return None


def extract_teams_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    teams_raw: list[Any] = []
    if "sports" in payload:
        try:
            teams_raw = payload["sports"][0]["leagues"][0]["teams"]
        except (IndexError, KeyError, TypeError):
            teams_raw = []
    elif "teams" in payload:
        teams_raw = payload["teams"]
    out: list[dict[str, Any]] = []
    for item in teams_raw:
        t = item.get("team", item) if isinstance(item, dict) else item
        if isinstance(t, dict):
            out.append(t)
    return out


def _pagination_meta(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return (page_count, total_count) when ESPN includes them."""
    league = _league_from_teams_payload(payload) or {}
    for src in (league, payload):
        if not isinstance(src, dict):
            continue
        page_count = src.get("pageCount")
        total = src.get("count") or src.get("total")
        pc = int(page_count) if page_count is not None else None
        tc = int(total) if total is not None else None
        if pc is not None or tc is not None:
            return pc, tc
    return None, None


def team_from_payload(
    t: dict[str, Any],
    conference_id: str = "",
    conference_name: str = "",
) -> TeamInfo | None:
    team_id = str(t.get("id", "")).strip()
    if not team_id:
        return None
    return TeamInfo(
        team_id=team_id,
        team_abbr=str(t.get("abbreviation", "")).strip(),
        team_name=str(t.get("displayName", t.get("name", ""))).strip(),
        conference_id=conference_id,
        conference_name=conference_name,
    )


def conference_from_groups(
    groups: Any,
    conf_name_by_id: dict[str, str],
) -> tuple[str, str]:
    if not isinstance(groups, dict):
        return "", ""
    cid = str(groups.get("id", "")).strip()
    if not cid:
        return "", ""
    return cid, conf_name_by_id.get(cid, "")


def cfb_conference_name_map(session: requests.Session) -> dict[str, str]:
    """Map ESPN conference group id -> display name (FBS/FCS standings)."""
    payload = get_json(session, CFB_STANDINGS_URL)
    mapping: dict[str, str] = {}
    for child in payload.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        cid = str(child.get("id", "")).strip()
        name = str(child.get("name", "")).strip()
        if cid and name:
            mapping[cid] = name
    return mapping


def extract_roster_athletes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Roster endpoint (position blocks) or team?enable=roster (flat athletes)."""
    athletes_raw: list[Any] = []
    if isinstance(payload.get("athletes"), list):
        athletes_raw = payload["athletes"]
    else:
        team = payload.get("team")
        if isinstance(team, dict) and isinstance(team.get("athletes"), list):
            athletes_raw = team["athletes"]

    athletes: list[dict[str, Any]] = []
    for block in athletes_raw or []:
        if not isinstance(block, dict):
            continue
        items = block.get("items")
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                a = it.get("athlete")
                if isinstance(a, dict):
                    athletes.append(a)
                elif it.get("id"):
                    athletes.append(it)
        elif block.get("id") and (
            block.get("displayName") or block.get("fullName") or block.get("shortName")
        ):
            athletes.append(block)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for a in athletes:
        aid = str(a.get("id", "")).strip()
        if aid and aid not in seen:
            seen.add(aid)
            unique.append(a)
    return unique


def _position_label(pos: Any) -> str:
    if isinstance(pos, dict):
        return str(
            pos.get("abbreviation")
            or pos.get("displayName")
            or pos.get("name")
            or ""
        ).strip()
    return str(pos or "").strip()


def _status_label(st: Any) -> str:
    if isinstance(st, dict):
        return str(st.get("name") or st.get("abbreviation") or st.get("type") or "").strip()
    return str(st or "").strip()


def player_row(
    team: TeamInfo,
    sport: SportKey,
    athlete: dict[str, Any],
) -> dict[str, str]:
    return {
        "team_id": team.team_id,
        "team_abbr": team.team_abbr,
        "team_name": team.team_name,
        "conference_id": team.conference_id,
        "conference_name": team.conference_name,
        "sport": sport,
        "player_id": str(athlete.get("id", "")).strip(),
        "player_name": str(
            athlete.get("displayName") or athlete.get("fullName") or ""
        ).strip(),
        "position": _position_label(athlete.get("position")),
        "jersey": str(athlete.get("jersey", "") or "").strip(),
        "status": _status_label(athlete.get("status")),
    }


def fetch_nfl_teams(session: requests.Session) -> list[TeamInfo]:
    payload = get_json(session, NFL_TEAMS_URL)
    teams = []
    for t in extract_teams_list(payload):
        info = team_from_payload(t)
        if info:
            teams.append(info)
    return teams


def fetch_cfb_teams(session: requests.Session) -> list[TeamInfo]:
    """
    All teams ESPN returns — paginate with &page=N until no teams or pageCount exhausted.
    """
    teams_by_id: dict[str, TeamInfo] = {}
    page = 1
    page_count: int | None = None

    while True:
        url = f"{CFB_TEAMS_URL}?limit={CFB_TEAMS_PAGE_LIMIT}&page={page}"
        payload = get_json(session, url)
        time.sleep(REQUEST_DELAY_S)

        pc, total = _pagination_meta(payload)
        if page_count is None and pc is not None:
            page_count = pc
            print(f"CFB: teams API reports pageCount={page_count}, count={total}")

        batch = extract_teams_list(payload)
        if not batch:
            break

        for t in batch:
            info = team_from_payload(t)
            if info:
                teams_by_id[info.team_id] = info

        print(f"CFB: teams page {page} — {len(batch)} teams ({len(teams_by_id)} unique so far)")

        if page_count is not None and page >= page_count:
            break
        if len(batch) < CFB_TEAMS_PAGE_LIMIT:
            break
        page += 1

    return list(teams_by_id.values())


def pull_sport_rosters(
    session: requests.Session,
    sport: SportKey,
    teams: list[TeamInfo],
    roster_url_template: str,
) -> list[dict[str, str]]:
    label = sport.upper()
    rows: list[dict[str, str]] = []
    for team in teams:
        print(f"{label}: fetching {team.team_name}...")
        url = roster_url_template.format(team_id=team.team_id)
        payload = get_json(session, url)
        time.sleep(REQUEST_DELAY_S)
        for athlete in extract_roster_athletes(payload):
            pid = str(athlete.get("id", "")).strip()
            if not pid:
                continue
            rows.append(player_row(team, sport, athlete))
    return rows


def pull_cfb_rosters(
    session: requests.Session,
    teams: list[TeamInfo],
    conf_name_by_id: dict[str, str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for team in teams:
        print(f"CFB: fetching {team.team_name}...")
        url = CFB_TEAM_ROSTER_URL.format(team_id=team.team_id)
        payload = get_json(session, url)
        time.sleep(REQUEST_DELAY_S)

        team_blob = payload.get("team") if isinstance(payload.get("team"), dict) else {}
        cid, cname = conference_from_groups(team_blob.get("groups"), conf_name_by_id)
        team = TeamInfo(
            team_id=team.team_id,
            team_abbr=team.team_abbr,
            team_name=team.team_name,
            conference_id=cid,
            conference_name=cname,
        )

        for athlete in extract_roster_athletes(payload):
            pid = str(athlete.get("id", "")).strip()
            if not pid:
                continue
            rows.append(player_row(team, "cfb", athlete))
    return rows


def write_outputs(sport: SportKey, rows: list[dict[str, str]]) -> None:
    ROSTER_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = ROSTER_DIR / f"{sport}_rosters.csv"
    json_path = ROSTER_DIR / f"{sport}_rosters.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"  Wrote {csv_path} ({len(rows)} players)")
    print(f"  Wrote {json_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pull NFL/CFB rosters from ESPN.")
    p.add_argument(
        "--sport",
        choices=("nfl", "cfb", "all"),
        default="all",
        help="Which sport(s) to pull (default: all).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    session = _session()
    summary: list[tuple[str, int, int]] = []

    if args.sport in ("nfl", "all"):
        nfl_teams = fetch_nfl_teams(session)
        time.sleep(REQUEST_DELAY_S)
        nfl_rows = pull_sport_rosters(session, "nfl", nfl_teams, NFL_ROSTER_URL)
        write_outputs("nfl", nfl_rows)
        summary.append(("NFL", len(nfl_teams), len(nfl_rows)))

    if args.sport in ("cfb", "all"):
        conf_name_by_id = cfb_conference_name_map(session)
        time.sleep(REQUEST_DELAY_S)
        cfb_teams = fetch_cfb_teams(session)
        time.sleep(REQUEST_DELAY_S)
        cfb_rows = pull_cfb_rosters(session, cfb_teams, conf_name_by_id)
        write_outputs("cfb", cfb_rows)
        summary.append(("CFB", len(cfb_teams), len(cfb_rows)))

    print("\n=== Summary ===")
    for name, n_teams, n_players in summary:
        print(f"{name}: {n_teams} teams, {n_players} players")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
