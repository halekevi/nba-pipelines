#!/usr/bin/env python3
"""
Step 3b — Attach Opposing Starting Goalie Context (NHL).

Reads step3 output, fetches probable/starting goalies per game_id from NHL gamecenter,
and attaches opposing goalie_name + goalie_sv_pct to each slate row.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path


HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NHL_WEB = "https://api-web.nhle.com/v1"


def read_csv(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def fetch_json(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _parse_game_date(row: dict) -> str:
    raw = str(row.get("game_start", "") or row.get("game_date", "") or row.get("fetched_at", "")).strip()
    if not raw:
        return ""
    try:
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return ts.date().isoformat()
    except Exception:
        return ""


def resolve_nhl_gamecenter_id(game_date: str, away: str, home: str) -> str:
    """
    PrizePicks game_id is not the NHL gamecenter id. Resolve via NHL schedule endpoint.
    Returns "" if not found.
    """
    if not (game_date and away and home):
        return ""
    data = fetch_json(f"{NHL_WEB}/schedule/{game_date}")
    for day in (data.get("gameWeek") or []):
        if str(day.get("date", ""))[:10] != game_date:
            continue
        for g in (day.get("games") or []):
            a = str(((g.get("awayTeam") or {}).get("abbrev") or "")).strip().upper()
            h = str(((g.get("homeTeam") or {}).get("abbrev") or "")).strip().upper()
            if a == away and h == home:
                gid = g.get("id") or g.get("gameId") or g.get("game_id")
                return str(gid or "").strip()
    return ""


def _to_float(v):
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def _pick_goalie(goalies: list[dict]) -> dict:
    if not isinstance(goalies, list) or not goalies:
        return {}
    for g in goalies:
        if str(g.get("starter", "")).strip().lower() in ("true", "1", "yes"):
            return g
    for g in goalies:
        toi = str(g.get("toi", "") or "").strip()
        if toi and toi not in ("00:00", "0:00"):
            return g
    return goalies[0]


def _extract_save_pct(player_landing: dict):
    rt = player_landing.get("careerTotals", {}) if isinstance(player_landing, dict) else {}
    rs = rt.get("regularSeason", {}) if isinstance(rt, dict) else {}
    v = _to_float(rs.get("savePct"))  # legacy
    if v is not None:
        return round(v, 3)
    v = _to_float(rs.get("savePctg"))  # current NHL API
    if v is not None:
        return round(v, 3)
    fs = player_landing.get("featuredStats", {}) if isinstance(player_landing, dict) else {}
    reg = fs.get("regularSeason", {}) if isinstance(fs, dict) else {}
    sub = reg.get("subSeason", {}) if isinstance(reg, dict) else {}
    v = _to_float(sub.get("savePct")) or _to_float(sub.get("savePctg"))
    return round(v, 3) if v is not None else None


def _name_part(v) -> str:
    if isinstance(v, dict):
        return str(v.get("default", "") or "").strip()
    return str(v or "").strip()


def _goalie_from_team(team_block: dict) -> tuple[str, float | None]:
    g = _pick_goalie(team_block.get("goalies", []) if isinstance(team_block, dict) else [])
    if not g:
        return "", None
    goalie_id = g.get("playerId") or g.get("id")
    name = _name_part(g.get("name"))
    if not goalie_id:
        return name, None
    landing = fetch_json(f"{NHL_WEB}/player/{goalie_id}/landing")
    return name, _extract_save_pct(landing)


def _abbr(team_block: dict) -> str:
    if not isinstance(team_block, dict):
        return ""
    ab = team_block.get("abbrev")
    if isinstance(ab, str):
        return ab.strip().upper()
    if isinstance(ab, dict):
        return str(ab.get("default", "") or "").strip().upper()
    return ""


def get_game_goalies(game_id: str, cache_dir: Path) -> dict:
    cache_path = cache_dir / f"goalies_{game_id}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            teams = cached.get("teams", {}) if isinstance(cached, dict) else {}
            # If cached payload is empty/invalid, refetch.
            if (
                isinstance(teams, dict)
                and any(str(v.get("goalie_name", "") or "").strip() for v in teams.values())
                and any(v.get("goalie_sv_pct") not in (None, "") for v in teams.values())
            ):
                return cached
        except Exception:
            pass

    out = {"teams": {}, "home_team": "", "away_team": ""}
    box = fetch_json(f"{NHL_WEB}/gamecenter/{game_id}/boxscore")
    home_meta = box.get("homeTeam", {}) if isinstance(box, dict) else {}
    away_meta = box.get("awayTeam", {}) if isinstance(box, dict) else {}
    stats = box.get("playerByGameStats", {}) if isinstance(box, dict) else {}
    home = (stats.get("homeTeam") or {}) if isinstance(stats, dict) else {}
    away = (stats.get("awayTeam") or {}) if isinstance(stats, dict) else {}

    home_ab = _abbr(home_meta)
    away_ab = _abbr(away_meta)
    out["home_team"] = home_ab
    out["away_team"] = away_ab
    if home_ab:
        n, sv = _goalie_from_team(home)
        out["teams"][home_ab] = {"goalie_name": n, "goalie_sv_pct": sv}
    if away_ab:
        n, sv = _goalie_from_team(away)
        out["teams"][away_ab] = {"goalie_name": n, "goalie_sv_pct": sv}

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=True), encoding="utf-8")
    time.sleep(0.5)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step3_nhl_with_defense.csv")
    ap.add_argument("--output", default="outputs/step3b_nhl_with_goalies.csv")
    args = ap.parse_args()

    rows = read_csv(args.input)
    script_dir = Path(__file__).resolve().parent
    cache_dir = script_dir.parent / "cache"

    # Resolve NHL gamecenter ids per unique matchup/date (PrizePicks game_id is not usable here)
    games: dict[tuple[str, str, str], str] = {}
    for r in rows:
        gd = _parse_game_date(r)
        away = str(r.get("away_team", "") or "").strip().upper()
        home = str(r.get("home_team", "") or "").strip().upper()
        if not (gd and away and home):
            continue
        key = (gd, away, home)
        if key not in games:
            games[key] = resolve_nhl_gamecenter_id(gd, away, home)

    goalie_by_game: dict[str, dict] = {}
    for nhl_gid in sorted({v for v in games.values() if v}):
        goalie_by_game[nhl_gid] = get_game_goalies(nhl_gid, cache_dir)

    out_rows = []
    for r in rows:
        row = dict(r)
        row["goalie_name"] = ""
        row["goalie_sv_pct"] = ""
        team = str(row.get("team", "") or "").strip().upper()
        gd = _parse_game_date(row)
        away = str(row.get("away_team", "") or "").strip().upper()
        home = str(row.get("home_team", "") or "").strip().upper()
        nhl_gid = games.get((gd, away, home), "")

        if nhl_gid and team and nhl_gid in goalie_by_game:
            g = goalie_by_game[nhl_gid]
            teams = g.get("teams", {})
            home = str(g.get("home_team", "") or "").strip().upper()
            away = str(g.get("away_team", "") or "").strip().upper()
            opp = away if team == home else (home if team == away else str(row.get("opponent", "") or "").strip().upper())
            opp_goalie = teams.get(opp, {})
            name = str(opp_goalie.get("goalie_name", "") or "").strip()
            sv = opp_goalie.get("goalie_sv_pct")
            row["goalie_name"] = name
            row["goalie_sv_pct"] = "" if sv in (None, "") else f"{float(sv):.3f}"
        out_rows.append(row)

    write_csv(out_rows, args.output)
    print(f"Saved {len(out_rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
