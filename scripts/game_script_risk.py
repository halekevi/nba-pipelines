#!/usr/bin/env python3
"""
Game script risk multiplier from spreads/totals in game_lines.db.
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

ensure_local_cache(str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "cache" / "game_lines.db"

# Pipeline team variants for DB lookup (same conventions as fetch_game_lines)
NBA_SLATE_VARIANTS: dict[str, tuple[str, ...]] = {
    "BRK": ("BRK", "BKN"),
    "BKN": ("BKN", "BRK"),
    "GS": ("GS", "GSW"),
    "GSW": ("GSW", "GS"),
    "NO": ("NO", "NOP"),
    "NOP": ("NOP", "NO"),
    "NY": ("NY", "NYK"),
    "NYK": ("NYK", "NY"),
    "SA": ("SA", "SAS"),
    "SAS": ("SAS", "SA"),
    "PHO": ("PHO", "PHX"),
    "PHX": ("PHX", "PHO"),
    "WAS": ("WAS", "WSH"),
    "WSH": ("WSH", "WAS"),
}

NHL_SLATE_VARIANTS: dict[str, tuple[str, ...]] = {
    "LA": ("LA", "LAK"),
    "LAK": ("LAK", "LA"),
    "NJ": ("NJ", "NJD"),
    "NJD": ("NJD", "NJ"),
    "SJ": ("SJ", "SJS"),
    "SJS": ("SJS", "SJ"),
    "TB": ("TB", "TBL"),
    "TBL": ("TBL", "TB"),
    "CLB": ("CLB", "CBJ"),
    "CBJ": ("CBJ", "CLB"),
    "ARZ": ("ARZ", "UTA"),
    "UTA": ("UTA", "ARZ"),
}


def _team_lookup_keys(sport: str, team: str) -> list[str]:
    t = str(team or "").strip().upper()
    if not t:
        return []
    if sport == "NBA" and t in NBA_SLATE_VARIANTS:
        return list(dict.fromkeys(NBA_SLATE_VARIANTS[t]))
    if sport == "NHL" and t in NHL_SLATE_VARIANTS:
        return list(dict.fromkeys(NHL_SLATE_VARIANTS[t]))
    return [t]


def spread_risk_multiplier(team_margin: float | None) -> float:
    """team_margin > 0 = team favored by that many points/goals (approx for NHL/soccer)."""
    if team_margin is None:
        return 0.97
    m = float(team_margin)
    if m >= 7.0:
        return 1.05
    if m >= 3.0:
        return 1.02
    if m > -0.5:
        return 1.00
    if m >= -3.0:
        return 0.98
    if m >= -6.5:
        return 0.93
    if m >= -9.5:
        return 0.87
    return 0.80


def total_risk_multiplier(sport: str, total: float | None) -> float:
    if total is None:
        return 1.00
    t = float(total)
    if sport in ("NBA", "CBB"):
        if t > 240:
            return 1.05
        if t >= 220:
            return 1.02
        if t >= 210:
            return 1.00
        if t >= 200:
            return 0.97
        return 0.93
    if sport == "NHL":
        if t > 6.5:
            return 1.05
        if t >= 5.5:
            return 1.00
        return 0.95
    if sport == "Soccer":
        if t > 2.5:
            return 1.05
        if t >= 2.0:
            return 1.00
        return 0.95
    return 1.00


def _norm_prop(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def prop_script_sensitivity(sport: str, prop_type: str) -> float:
    p = _norm_prop(prop_type)

    if sport in ("NBA", "CBB"):
        if any(x in p for x in ("points rebounds assists", "pra")) or p == "pra":
            return 0.9
        if "fantasy" in p:
            return 0.9
        if "points assists" in p or p.endswith(" pts asts") or "pts ast" in p:
            return 0.9
        if "points rebounds" in p or "pts reb" in p:
            return 0.9
        if "point" in p or p == "pts" or p.startswith("pts "):
            return 1.0
        if "assist" in p or p == "ast":
            return 0.7
        if "rebound" in p or p == "reb":
            return 0.6
        if "steal" in p or p == "stl":
            return 0.3
        if "block" in p or p == "blk":
            return 0.3
        if "turnover" in p or "to " in p or p == "to":
            return 0.4
        if "three" in p or "3pt" in p or "3 pt" in p or "fg3" in p:
            return 0.8
        if "free throw" in p or p.startswith("ft") or " fta" in p:
            return 0.7
        return 0.85

    if sport == "NHL":
        if "save" in p:
            return -0.3
        if "goal" in p and "against" not in p:
            return 1.0
        if "assist" in p:
            return 0.9
        if "point" in p:
            return 0.95
        if "shot" in p:
            return 0.5
        return 0.85

    if sport == "Soccer":
        if "save" in p or "goalie" in p:
            return -0.3
        if "goal" in p and "keeper" not in p:
            return 1.0
        if "assist" in p:
            return 0.9
        if "shot" in p:
            return 0.5
        if "pass" in p:
            return 0.2
        if "tackle" in p:
            return 0.2
        return 0.5

    return 0.85


def _load_game_row(
    conn: sqlite3.Connection,
    sport: str,
    game_date: str,
    team: str,
) -> tuple[dict[str, Any] | None, str | None, str | None, float | None]:
    keys = _team_lookup_keys(sport, team)
    if not keys:
        return None, None, None, None
    ph = ",".join("?" * len(keys))
    q = (
        f"SELECT home_team, away_team, spread, total FROM game_lines "
        f"WHERE sport = ? AND game_date = ? AND (home_team IN ({ph}) OR away_team IN ({ph}))"
    )
    params: list[Any] = [sport, game_date] + keys + keys
    cur = conn.execute(q, params)
    row = cur.fetchone()
    if not row:
        return None, None, None, None
    home, away, spread, total = row[0], row[1], row[2], row[3]
    is_home = str(home).upper() in keys
    is_away = str(away).upper() in keys
    team_margin: float | None = None
    if spread is not None and (is_home or is_away):
        sp = float(spread)
        if is_home:
            team_margin = -sp
        else:
            team_margin = sp
    return (
        {"home": home, "away": away, "spread": spread, "total": total},
        home,
        away,
        team_margin,
    )


def _combined_multiplier(
    sport: str,
    team_margin: float | None,
    total: float | None,
    prop_type: str,
    home: str | None,
    away: str | None,
    player_team: str,
) -> tuple[float, str]:
    spr = spread_risk_multiplier(team_margin)
    tr = total_risk_multiplier(sport, total)
    sens = prop_script_sensitivity(sport, prop_type)

    if sport in ("NHL", "Soccer") and sens < 0 and team_margin is not None and team_margin < 0:
        spread_term = 0.3 * abs(spr - 1.0)
        raw = 1.0 + spread_term + (tr - 1.0) * abs(sens) * 0.5
    else:
        raw = 1.0 + (spr - 1.0) * sens + (tr - 1.0) * sens * 0.5

    mult = max(0.75, min(1.10, raw))

    opp = ""
    if home and away:
        pu = str(player_team).strip().upper()
        if pu == str(home).strip().upper():
            opp = str(away)
        elif pu == str(away).strip().upper():
            opp = str(home)

    margin_s = f"{team_margin:+.1f}" if team_margin is not None else "?"
    fav_dog = "pick"
    if team_margin is not None:
        if team_margin >= 3:
            fav_dog = "fav"
        elif team_margin <= -3:
            fav_dog = "dog"

    reason = (
        f"{str(player_team).upper()} {margin_s} pt margin vs {opp or '?'}, "
        f"{prop_type}: {mult:.2f}x game script ({fav_dog}; spread_risk {spr:.2f}, total_risk {tr:.2f})"
    )
    if team_margin is None:
        reason = f"No spread data: {mult:.2f}x default"

    return mult, reason


def get_game_script_multiplier(
    player_team: str,
    sport: str,
    prop_type: str,
    game_date: str,
    db_path: str | None = None,
) -> tuple[float, str]:
    try:
        dbp = Path(db_path) if db_path else DEFAULT_DB
        if not dbp.is_file():
            return 0.97, "No spread data: 0.97x default"

        conn = sqlite3.connect(str(dbp))
        try:
            game, home, away, team_margin = _load_game_row(
                conn, sport.strip().upper(), str(game_date).strip()[:10], player_team
            )
        finally:
            conn.close()

        if not game:
            return 0.97, "No spread data: 0.97x default"

        total = game.get("total")
        if isinstance(total, str):
            try:
                total = float(total)
            except ValueError:
                total = None

        mult, reason = _combined_multiplier(
            sport.strip().upper(),
            team_margin,
            float(total) if total is not None else None,
            prop_type,
            str(home) if home else None,
            str(away) if away else None,
            str(player_team),
        )
        return mult, reason
    except Exception:
        return 0.97, "No spread data: 0.97x default"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--sport", required=True)
    ap.add_argument("--prop", required=True)
    ap.add_argument("--date", default="")
    ap.add_argument("--db", default="")
    args = ap.parse_args()
    from datetime import datetime, timezone

    gd = args.date.strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dbp = args.db.strip() or None
    m, note = get_game_script_multiplier(args.team, args.sport, args.prop, gd, dbp)
    print(f"multiplier={m:.4f}")
    print(note)


if __name__ == "__main__":
    main()
