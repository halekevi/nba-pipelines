#!/usr/bin/env python3
"""
build_ncaa_mbb_espn_athletes_master.py

Build a master table of ESPN Men's NCAA basketball athlete IDs by:
- fetching ESPN teams (D1)
- fetching each team's roster
- extracting athlete id + name + team metadata
- saving a single master CSV

Usage:
  py -3.14 build_ncaa_mbb_espn_athletes_master.py --out ncaa_mbb_athletes_master.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def norm_name(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s'-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s).strip()
    return re.sub(r"\s+", " ", s)


def http_get_json(url: str, timeout: int = 30, retries: int = 4):
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Failed GET {url} after {retries} retries. Last error: {last_err}")


def teams_endpoint(page: int, limit: int) -> str:
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit={limit}&page={page}"


def roster_endpoint(team_id: str) -> str:
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{team_id}/roster"


@dataclass
class Team:
    team_id: str
    name: str
    abbrev: str


def extract_teams(payload: Dict[str, Any]) -> List[Team]:
    out = []
    teams = []
    if "sports" in payload:
        try:
            teams = payload["sports"][0]["leagues"][0]["teams"]
        except Exception:
            teams = []
    elif "teams" in payload:
        teams = payload["teams"]

    for item in teams:
        t = item.get("team", item)
        team_id = str(t.get("id", "")).strip()
        if not team_id:
            continue
        name = str(t.get("displayName", t.get("name", ""))).strip()
        abbrev = str(t.get("abbreviation", "")).strip()
        out.append(Team(team_id=team_id, name=name, abbrev=abbrev))
    return out


def extract_roster(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    athletes = []
    blocks = payload.get("athletes", [])
    for b in blocks:
        if isinstance(b, dict) and isinstance(b.get("items"), list):
            for it in b["items"]:
                a = it.get("athlete") or it
                if isinstance(a, dict):
                    athletes.append(a)
        elif isinstance(b, dict):
            a = b.get("athlete") or b
            if isinstance(a, dict):
                athletes.append(a)
    seen = set()
    out = []
    for a in athletes:
        aid = str(a.get("id", "")).strip()
        if aid and aid not in seen:
            seen.add(aid)
            out.append(a)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ncaa_mbb_athletes_master.csv")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--max_pages", type=int, default=50)
    args = ap.parse_args()

    all_teams = []
    for page in range(1, args.max_pages + 1):
        payload = http_get_json(teams_endpoint(page, args.limit))
        teams = extract_teams(payload)
        if not teams:
            break
        all_teams.extend(teams)
        if len(teams) < args.limit:
            break

    uniq = {t.team_id: t for t in all_teams}
    teams_list = list(uniq.values())
    print(f"Found teams: {len(teams_list)}")

    rows = []

    for i, t in enumerate(teams_list, start=1):
        try:
            payload = http_get_json(roster_endpoint(t.team_id))
            athletes = extract_roster(payload)
            for a in athletes:
                aid = str(a.get("id", "")).strip()
                name = str(a.get("displayName", a.get("fullName", ""))).strip()
                if not aid:
                    continue
                rows.append({
                    "espn_athlete_id": aid,
                    "athlete_name": name,
                    "athlete_name_norm": norm_name(name),
                    "team_id": t.team_id,
                    "team_name": t.name,
                    "team_abbr": t.abbrev
                })
        except Exception as e:
            print(f"Failed roster for {t.name}: {e}")

        if i % 25 == 0 or i == len(teams_list):
            print(f"[{i}/{len(teams_list)}] processed")

        time.sleep(0.3)

    dedup = {r["espn_athlete_id"]: r for r in rows}
    final_rows = list(dedup.values())
    print(f"Unique athletes captured: {len(final_rows)}")

    fieldnames = [
        "espn_athlete_id", "athlete_name", "athlete_name_norm",
        "team_id", "team_name", "team_abbr"
    ]

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in sorted(final_rows, key=lambda x: (x["team_abbr"], x["athlete_name_norm"])):
            w.writerow(r)

    print(f"Written: {args.out}")


if __name__ == "__main__":
    main()
