"""
Single source of truth: map PrizePicks / ticket ``league`` strings to PropOracle sport keys.

``historical_actuals.db`` / ``fetch_historical_actuals.py`` only ingest **NBA**, **CBB**, **NHL**,
and **Soccer**. Other leagues must return ``None`` so players are never merged into the wrong
sport bucket or matched against the wrong game-log rows.
"""

from __future__ import annotations

# Sports with rows in player_game_logs (see fetch_historical_actuals.create_db).
HISTORICAL_SPORTS: frozenset[str] = frozenset({"NBA", "CBB", "NHL", "Soccer"})


def league_to_sport(league: str | None) -> str | None:
    if league is None:
        return None
    u = str(league).upper().strip()
    if not u:
        return None

    # Explicit non-target leagues (do not guess basketball).
    if any(
        x in u
        for x in (
            "MLB",
            "NFL",
            "UFC",
            "MMA",
            "PGA",
            "TENNIS",
            "WNBA",
            "CWBB",
            "WCBB",
        )
    ):
        return None

    if "NBA" in u or u == "BASKETBALL":
        return "NBA"
    if "CFB" in u or "NCAAF" in u or ("COLLEGE" in u and "FOOTBALL" in u):
        return "CFB"
    if "CBB" in u or "NCAAB" in u or ("COLLEGE" in u and "BASKETBALL" in u) or u == "NCAAB":
        return "CBB"
    if "NHL" in u or u == "HOCKEY":
        return "NHL"
    if any(
        x in u
        for x in (
            "EPL",
            "MLS",
            "UCL",
            "LALIGA",
            "BUNDESLIGA",
            "SOCCER",
            "SERIE A",
            "NWSL",
            "LIGAMX",
            "EFL",
        )
    ):
        return "Soccer"
    if u in ("SOC", "SOCCER"):
        return "Soccer"
    return None


def assert_historical_sport(sport: str) -> None:
    if sport not in HISTORICAL_SPORTS:
        raise ValueError(f"Unsupported historical sport {sport!r} (expected one of {sorted(HISTORICAL_SPORTS)})")
