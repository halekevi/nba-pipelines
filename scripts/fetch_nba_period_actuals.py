#!/usr/bin/env python3
"""
Build NBA period-specific actuals from ESPN play-by-play.

Outputs use the same schema as fetch_actuals.py:
  player, team, prop_type, actual (+ raw stat columns)

Examples:
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 1Q --output outputs/2026-03-25/actuals_nba1q_2026-03-25.csv
  py -3.14 scripts/fetch_nba_period_actuals.py --date 2026-03-25 --segment 1H --output outputs/2026-03-25/actuals_nba1h_2026-03-25.csv
"""

from __future__ import annotations

import argparse
import re
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

CORE_PBP_URL = "https://cdn.espn.com/core/nba/playbyplay?gameId={event_id}&xhr=1"


def _default_date_str() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


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


def _parse_game_period_stats(event_id: str, max_period: int) -> list[dict]:
    r = requests.get(CORE_PBP_URL.format(event_id=event_id), headers=HEADERS, timeout=25)
    r.raise_for_status()
    payload = r.json() or {}
    gp = payload.get("gamepackageJSON", {}) or {}
    plays = gp.get("plays", []) or []
    if not plays:
        return []

    athlete_meta = _athlete_index(gp)
    stats_by_player: dict[str, dict[str, float]] = {}

    for play in plays:
        pnum = int((play.get("period") or {}).get("number") or 0)
        if pnum < 1 or pnum > max_period:
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
        rows.extend(parse_stats(player_name, team_abbr, s))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_default_date_str(), help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--segment", choices=["1Q", "1H"], required=True, help="Target period segment")
    ap.add_argument("--output", required=True, help="Output CSV path")
    args = ap.parse_args()

    max_period = 1 if args.segment == "1Q" else 2
    # fetch_events_for_date prefixes "basketball/" internally for hoops sports.
    events = fetch_events_for_date("nba", args.date, is_cbb=False)
    if not events:
        outp = Path(args.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=["player", "team", "prop_type", "actual"]).to_csv(outp, index=False)
        print(f"No NBA events found for {args.date} - wrote stub -> {outp}")
        return

    all_rows: list[dict] = []
    event_ids = sorted({str((e or {}).get("id", "")).strip() for e in events if (e or {}).get("id")})
    for eid in event_ids:
        try:
            all_rows.extend(_parse_game_period_stats(eid, max_period=max_period))
        except Exception as e:
            print(f"WARNING: failed period parse for event {eid}: {e}")

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
    print(f"Saved {args.segment} NBA actuals -> {outp}  ({len(df)} rows)")


if __name__ == "__main__":
    main()

