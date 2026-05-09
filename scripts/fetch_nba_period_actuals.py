#!/usr/bin/env python3
"""
Build basketball period-specific actuals.

NBA (default): prefers NBA.com boxscoretraditionalv2 with StartPeriod/EndPeriod,
which returns stats summed across those quarters (e.g. 1H = Q1+Q2, 2H = Q3+Q4).

CBB (--sport CBB): NBA.com is not used; ESPN play-by-play is parsed for the same
period ranges (coarser than full box; use for period props when needed).

Outputs use the same schema as fetch_actuals.py:
  player, team, prop_type, actual (+ raw stat columns)

Examples:
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 1Q --output outputs/2026-03-25/actuals_nba1q_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 2Q --output outputs/2026-03-25/actuals_nba2q_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 3Q --output outputs/2026-03-25/actuals_nba3q_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 4Q --output outputs/2026-03-25/actuals_nba4q_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 1H --output outputs/2026-03-25/actuals_nba1h_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 2H --output outputs/2026-03-25/actuals_nba2h_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --sport CBB --date 2026-03-25 --segment 1H --output outputs/2026-03-25/actuals_cbb1h_2026-03-25.csv
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from fetch_actuals import (
    ESPN_TO_SLATE_ABBREV,
    HEADERS,
    fetch_events_for_date,
    parse_stats,
)

# ESPN core XHR (same JSON shape for NBA and men's CBB).
CORE_PBP_URL = "https://cdn.espn.com/core/{sport}/playbyplay?gameId={event_id}&xhr=1"
NBA_SCOREBOARD_URL = "https://stats.nba.com/stats/scoreboardv2"
NBA_BOXSCORE_URL = "https://stats.nba.com/stats/boxscoretraditionalv2"

# Regulation-only halves: 2H = Q3 + Q4 (no OT in these ranges).
SEGMENT_TO_PERIODS: dict[str, tuple[int, int]] = {
    "1Q": (1, 1),
    "2Q": (2, 2),
    "3Q": (3, 3),
    "4Q": (4, 4),
    "1H": (1, 2),
    "2H": (3, 4),
}


def _default_date_str() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _nba_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nba.com/",
        "Origin": "https://www.nba.com",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }


def _req_json(url: str, params: dict, headers: dict[str, str], timeout: int = 30, retries: int = 2) -> dict:
    last_err: Exception | None = None
    for _ in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return {}


def _nba_game_ids_for_date(date_str: str) -> list[str]:
    mm, dd, yyyy = date_str.split("-")[1], date_str.split("-")[2], date_str.split("-")[0]
    params = {"DayOffset": 0, "GameDate": f"{mm}/{dd}/{yyyy}", "LeagueID": "00"}
    # Keep this short so backfills quickly fall back to ESPN when NBA.com stalls.
    payload = _req_json(NBA_SCOREBOARD_URL, params=params, headers=_nba_headers(), timeout=8, retries=0)
    result_sets = payload.get("resultSets") or []
    for rs in result_sets:
        if str(rs.get("name")) == "GameHeader":
            headers = rs.get("headers") or []
            rows = rs.get("rowSet") or []
            try:
                i_gid = headers.index("GAME_ID")
            except ValueError:
                return []
            out = []
            for row in rows:
                gid = str(row[i_gid]).strip()
                if gid:
                    out.append(gid)
            return sorted(set(out))
    return []


def _nba_boxscore_period_rows(game_id: str, start_period: int, end_period: int) -> list[dict]:
    params = {
        "GameID": game_id,
        "StartPeriod": start_period,
        "EndPeriod": end_period,
        "StartRange": 0,
        "EndRange": 0,
        "RangeType": 0,
    }
    payload = _req_json(NBA_BOXSCORE_URL, params=params, headers=_nba_headers(), timeout=45, retries=1)
    result_sets = payload.get("resultSets") or []
    player_rs = next((x for x in result_sets if str(x.get("name")) == "PlayerStats"), None)
    if not player_rs:
        return []

    headers = player_rs.get("headers") or []
    rows = player_rs.get("rowSet") or []
    if not headers or not rows:
        return []

    idx = {h: i for i, h in enumerate(headers)}
    out: list[dict] = []
    for row in rows:
        player_name = str(row[idx.get("PLAYER_NAME", -1)]).strip() if "PLAYER_NAME" in idx else ""
        team_raw = str(row[idx.get("TEAM_ABBREVIATION", -1)]).strip().upper() if "TEAM_ABBREVIATION" in idx else ""
        if not player_name or not team_raw:
            continue
        team_abbr = ESPN_TO_SLATE_ABBREV.get(team_raw, team_raw)
        stat_map = {
            "PTS": float(row[idx["PTS"]]) if "PTS" in idx and str(row[idx["PTS"]]).strip() else 0.0,
            "REB": float(row[idx["REB"]]) if "REB" in idx and str(row[idx["REB"]]).strip() else 0.0,
            "AST": float(row[idx["AST"]]) if "AST" in idx and str(row[idx["AST"]]).strip() else 0.0,
            "BLK": float(row[idx["BLK"]]) if "BLK" in idx and str(row[idx["BLK"]]).strip() else 0.0,
            "STL": float(row[idx["STL"]]) if "STL" in idx and str(row[idx["STL"]]).strip() else 0.0,
            "TO": float(row[idx["TO"]]) if "TO" in idx and str(row[idx["TO"]]).strip() else 0.0,
            "FGM": float(row[idx["FGM"]]) if "FGM" in idx and str(row[idx["FGM"]]).strip() else 0.0,
            "FGA": float(row[idx["FGA"]]) if "FGA" in idx and str(row[idx["FGA"]]).strip() else 0.0,
            "3PM": float(row[idx["FG3M"]]) if "FG3M" in idx and str(row[idx["FG3M"]]).strip() else 0.0,
            "3PA": float(row[idx["FG3A"]]) if "FG3A" in idx and str(row[idx["FG3A"]]).strip() else 0.0,
            "FTM": float(row[idx["FTM"]]) if "FTM" in idx and str(row[idx["FTM"]]).strip() else 0.0,
            "FTA": float(row[idx["FTA"]]) if "FTA" in idx and str(row[idx["FTA"]]).strip() else 0.0,
            "OREB": float(row[idx["OREB"]]) if "OREB" in idx and str(row[idx["OREB"]]).strip() else 0.0,
            "DREB": float(row[idx["DREB"]]) if "DREB" in idx and str(row[idx["DREB"]]).strip() else 0.0,
            "MIN": float(row[idx["MIN"]]) if "MIN" in idx and str(row[idx["MIN"]]).strip() else 0.0,
        }
        out.extend(parse_stats(player_name, team_abbr, stat_map))
    return out


def _add_stat(stats_by_player: dict[str, dict[str, float]], aid: str, key: str, val: float = 1.0) -> None:
    if not aid:
        return
    bucket = stats_by_player.setdefault(aid, {})
    bucket[key] = float(bucket.get(key, 0.0)) + float(val)


def _athlete_index(gamepackage: dict) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for team_block in gamepackage.get("boxscore", {}).get("players", []):
        abbr_raw = str(team_block.get("team", {}).get("abbreviation", "")).strip().upper()
        abbr = ESPN_TO_SLATE_ABBREV.get(abbr_raw, abbr_raw)
        for stat_group in team_block.get("statistics", []):
            for athlete in stat_group.get("athletes", []):
                a = athlete.get("athlete", {}) or {}
                aid = str(a.get("id", "")).strip()
                name = str(a.get("displayName", "")).strip()
                if aid and name:
                    out[aid] = (name, abbr)
    return out


def _parse_game_period_stats(
    event_id: str,
    start_period: int,
    end_period: int,
    espn_sport: str = "nba",
) -> list[dict]:
    """
    Sum stats from ESPN plays whose period is in [start_period, end_period] inclusive.
    Used as fallback for NBA when NBA.com fails, and as the primary path for CBB.
    """
    url = CORE_PBP_URL.format(sport=espn_sport, event_id=event_id)
    gp = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        payload = r.json() or {}
        gp = payload.get("gamepackageJSON", {}) or {}
    except Exception:
        # Core endpoint can intermittently return non-JSON/empty for NBA.
        # Fallback to site summary, which carries compatible plays/boxscore blocks.
        league = "nba" if espn_sport == "nba" else "mens-college-basketball"
        s_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/"
            f"{league}/summary?event={event_id}"
        )
        r2 = requests.get(s_url, headers=HEADERS, timeout=25)
        r2.raise_for_status()
        payload2 = r2.json() or {}
        gp = payload2.get("gamepackageJSON", payload2) or {}
    plays = gp.get("plays", []) or []
    if not plays:
        return []

    athlete_meta = _athlete_index(gp)
    stats_by_player: dict[str, dict[str, float]] = {}

    for play in plays:
        pnum = int((play.get("period") or {}).get("number") or 0)
        if pnum < start_period or pnum > end_period:
            continue

        text = str(play.get("text", "") or "")
        ltxt = text.lower()
        participants = [
            str((p.get("athlete") or {}).get("id", "")).strip()
            for p in (play.get("participants") or [])
            if isinstance(p, dict)
        ]
        participants = [p for p in participants if p]
        primary = participants[0] if participants else ""
        secondary = participants[1] if len(participants) > 1 else ""

        if "offensive rebound" in ltxt:
            _add_stat(stats_by_player, primary, "OREB", 1)
            _add_stat(stats_by_player, primary, "REB", 1)
        elif "defensive rebound" in ltxt:
            _add_stat(stats_by_player, primary, "DREB", 1)
            _add_stat(stats_by_player, primary, "REB", 1)
        elif " rebound" in ltxt and "team rebound" not in ltxt:
            # ESPN pbp sometimes logs generic "Player rebound" (no OREB/DREB label).
            # Count it toward total rebounds so PRA-style props don't undercount by 1.
            _add_stat(stats_by_player, primary, "REB", 1)

        if "turnover" in ltxt and "team turnover" not in ltxt:
            _add_stat(stats_by_player, primary, "TO", 1)

        if "assists)" in ltxt:
            _add_stat(stats_by_player, secondary, "AST", 1)
        if "steals)" in ltxt:
            _add_stat(stats_by_player, secondary, "STL", 1)
        if "blocks)" in ltxt:
            _add_stat(stats_by_player, secondary, "BLK", 1)

        if "free throw" in ltxt:
            _add_stat(stats_by_player, primary, "FTA", 1)
            if "makes free throw" in ltxt:
                _add_stat(stats_by_player, primary, "FTM", 1)
                _add_stat(stats_by_player, primary, "PTS", 1)
            continue

        made = " makes " in f" {ltxt} "
        missed = " misses " in f" {ltxt} "
        if not (made or missed):
            continue
        if "free throw" in ltxt:
            continue

        is_shot = bool(play.get("shootingPlay")) or any(
            k in ltxt for k in ("jumper", "jumpshot", "layup", "dunk", "hook shot", "tip shot", "shot")
        )
        if not is_shot:
            continue

        _add_stat(stats_by_player, primary, "FGA", 1)
        is_three = ("three point" in ltxt) or ("3-point" in ltxt)
        if is_three:
            _add_stat(stats_by_player, primary, "3PA", 1)
        else:
            _add_stat(stats_by_player, primary, "2PA", 1)

        if made:
            _add_stat(stats_by_player, primary, "FGM", 1)
            if is_three:
                _add_stat(stats_by_player, primary, "3PM", 1)
            else:
                _add_stat(stats_by_player, primary, "2PM", 1)
            score_val = play.get("scoreValue")
            try:
                pts = float(score_val)
            except Exception:
                pts = 3.0 if is_three else 2.0
            _add_stat(stats_by_player, primary, "PTS", pts)

    rows: list[dict] = []
    for aid, s in stats_by_player.items():
        meta = athlete_meta.get(aid)
        if not meta:
            continue
        player_name, team_abbr = meta
        # ESPN PBP fallback only increments stats when events are seen.
        # Ensure core counting stats exist at 0 so props like Assists 0.5 OVER
        # can be graded as MISS instead of remaining ungraded when a player logs 0.
        for k in (
            "PTS",
            "REB",
            "AST",
            "BLK",
            "STL",
            "TO",
            "FGM",
            "FGA",
            "3PM",
            "3PA",
            "FTM",
            "FTA",
            "OREB",
            "DREB",
            "MIN",
        ):
            s.setdefault(k, 0.0)
        rows.extend(parse_stats(player_name, team_abbr, s))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_default_date_str(), help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument(
        "--sport",
        choices=["NBA", "CBB"],
        default="NBA",
        help="NBA uses NBA.com box scores (ESPN PBP fallback). CBB uses ESPN PBP only.",
    )
    ap.add_argument(
        "--segment",
        choices=list(SEGMENT_TO_PERIODS.keys()),
        required=True,
        help="Period window: single quarter or half (1H=Q1+Q2, 2H=Q3+Q4 regulation).",
    )
    ap.add_argument("--output", required=True, help="Output CSV path")
    args = ap.parse_args()

    start_period, end_period = SEGMENT_TO_PERIODS[args.segment]
    all_rows: list[dict] = []
    use_nba_com = args.sport.upper() == "NBA"

    if use_nba_com:
        nba_ids: list[str] = []
        try:
            nba_ids = _nba_game_ids_for_date(args.date)
        except Exception as e:
            print(f"WARNING: NBA.com scoreboard fetch failed; will try ESPN fallback: {e}")

        for gid in nba_ids:
            try:
                all_rows.extend(
                    _nba_boxscore_period_rows(gid, start_period=start_period, end_period=end_period)
                )
            except Exception as e:
                print(f"WARNING: NBA.com boxscore parse failed for game {gid}: {e}")

    # ESPN play-by-play: CBB primary; NBA fallback when NBA.com returned nothing.
    if not all_rows:
        espn_path = "nba" if use_nba_com else "mens-college-basketball"
        events = fetch_events_for_date(espn_path, args.date, is_cbb=(espn_path == "mens-college-basketball"))
        event_ids = sorted({str((e or {}).get("id", "")).strip() for e in events if (e or {}).get("id")})
        label = "ESPN PBP fallback" if use_nba_com else "ESPN PBP (CBB)"
        print(f"  {label}: {len(event_ids)} events, periods {start_period}-{end_period}")
        for eid in event_ids:
            try:
                all_rows.extend(
                    _parse_game_period_stats(
                        eid,
                        start_period=start_period,
                        end_period=end_period,
                        espn_sport=espn_path,
                    )
                )
            except Exception as e:
                print(f"WARNING: ESPN period parse failed for event {eid}: {e}")

    df = pd.DataFrame(all_rows)
    if df.empty:
        df = pd.DataFrame(columns=["player", "team", "prop_type", "actual"])
    else:
        df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
        df = (
            df.sort_values("actual", ascending=False)
            .drop_duplicates(subset=["player", "team", "prop_type"], keep="first")
            .reset_index(drop=True)
        )

    outp = Path(args.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(outp, index=False)
    print(f"Saved {args.sport} {args.segment} actuals -> {outp}  ({len(df)} rows)")


if __name__ == "__main__":
    main()

