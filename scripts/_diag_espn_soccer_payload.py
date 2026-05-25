#!/usr/bin/env python3
"""One-off: inspect ESPN soccer summary payload for stat keys."""
import json
import re
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/cache/proporacle_ref.db"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def roster_stat_keys(data: dict) -> tuple[set[str], set[str]]:
    abbrevs, names = set(), set()
    for tb in data.get("rosters") or []:
        for entry in (tb.get("roster") or []):
            for s in entry.get("stats") or []:
                if isinstance(s, dict):
                    abbrevs.add(str(s.get("abbreviation", "")))
                    names.add(str(s.get("name", "")))
    return abbrevs, names


def main() -> None:
    game_id = "737122"
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/summary?event={game_id}"
    data = requests.get(url, headers=HEADERS, timeout=20).json()
    text = json.dumps(data)

    print("=== Step 1: de Roon rows in DB ===")
    con = sqlite3.connect(DB)
    for r in con.execute(
        "SELECT event_id, game_date, sh, tk, pa FROM soccer "
        "WHERE espn_player_id='146335' ORDER BY game_date DESC LIMIT 3"
    ):
        print(r)
    con.close()

    print("\n=== Step 2: ESPN summary event", game_id, "(ita.1) ===")
    print("root keys:", list(data.keys()))
    for pat in ("pass", "tackle", "clearance", "dribble"):
        print(f"  '{pat}' in JSON:", bool(re.search(pat, text, re.I)))

    rosters = data.get("rosters") or []
    for tb in rosters:
        for entry in (tb.get("roster") or []):
            if str((entry.get("athlete") or {}).get("id")) != "146335":
                continue
            print("\n--- de Roon entry.stats ---")
            for i, s in enumerate(entry.get("stats") or []):
                if isinstance(s, dict):
                    print(
                        i,
                        s.get("abbreviation"),
                        "|",
                        s.get("name"),
                        "|",
                        s.get("value"),
                    )
            break

    ab, nm = roster_stat_keys(data)
    print("\nAll abbreviations in match:", sorted(ab))
    print("All names in match:", sorted(nm))

    print("\n=== Compare eng.1 latest event ===")
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT event_id, league FROM soccer WHERE league='eng.1' "
        "ORDER BY game_date DESC LIMIT 1"
    ).fetchone()
    con.close()
    if row:
        eid, lg = row
        d2 = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/summary?event={eid}",
            headers=HEADERS,
            timeout=20,
        ).json()
        ab2, nm2 = roster_stat_keys(d2)
        print("event", eid, lg)
        print("abbreviations:", sorted(ab2))
        print("names:", sorted(nm2))

    print("\n=== boxscore.players (PATH 2) ===")
    players = (data.get("boxscore") or {}).get("players") or []
    print("teams:", len(players))
    for tb in players:
        t_abbr = (tb.get("team") or {}).get("abbreviation")
        for sg in (tb.get("statistics") or []):
            labels = sg.get("labels") or sg.get("keys") or sg.get("names")
            print(" ", t_abbr, "group", sg.get("name"), "labels", labels)
            for a in (sg.get("athletes") or []):
                if str((a.get("athlete") or {}).get("id")) == "146335":
                    print("   de Roon flat:", a.get("stats"))

    print("\n=== leaders (passes only — not full boxscore) ===")
    for block in data.get("leaders") or []:
        team = (block.get("team") or {}).get("abbreviation")
        for lg in (block.get("leaders") or []):
            if "pass" in str(lg.get("name", "")).lower():
                leader = ((lg.get("leaders") or [{}])[0])
                ath = (leader.get("athlete") or {})
                print(
                    " ",
                    team,
                    lg.get("displayName"),
                    ath.get("displayName"),
                    ath.get("id"),
                    leader.get("summary"),
                )


if __name__ == "__main__":
    main()
