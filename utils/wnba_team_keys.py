"""
WNBA team abbreviation normalization.

PrizePicks, ESPN cache, and ``wnba_defense_summary.csv`` use different codes for the
same franchise (e.g. LVA/LAS vs LV for Las Vegas Aces, NYL vs NY for Liberty).
"""

from __future__ import annotations

# Slate / PrizePicks abbrev -> ``wnba_defense_summary.csv`` TEAM_ABBREVIATION
WNBA_DEFENSE_TEAM_KEY_MAP: dict[str, str] = {
    "LVA": "LV",
    "LAS": "LV",
    "LV": "LV",
    "NYL": "NY",
    "NY": "NY",
    "GSV": "GS",
    "GS": "GS",
    "PHO": "PHX",
    "PHX": "PHX",
    "LA": "LA",
    "CON": "CON",
    "DAL": "DAL",
    "IND": "IND",
    "ATL": "ATL",
    "CHI": "CHI",
    "MIN": "MIN",
    "SEA": "SEA",
    "WSH": "WSH",
    "WAS": "WSH",
    "POR": "POR",
    "PDX": "POR",  # PrizePicks Portland Fire
    "TOR": "TOR",
}

# Canonical franchise key for same-game pairing (step2 opp inference)
WNBA_CANONICAL_TEAM_KEY: dict[str, str] = dict(WNBA_DEFENSE_TEAM_KEY_MAP)


def _norm(abbr: str) -> str:
    return str(abbr or "").strip().upper()


def defense_team_key(abbr: str) -> str:
    """Map slate opp code to defense-summary TEAM_ABBREVIATION."""
    a = _norm(abbr)
    if not a:
        return ""
    return WNBA_DEFENSE_TEAM_KEY_MAP.get(a, a)


def canonical_team_key(abbr: str) -> str:
    """Canonical franchise key for grouping teams in the same game."""
    a = _norm(abbr)
    if not a:
        return ""
    return WNBA_CANONICAL_TEAM_KEY.get(a, a)
