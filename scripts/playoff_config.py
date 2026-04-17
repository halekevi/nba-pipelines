#!/usr/bin/env python3
"""Shared playoff configuration for NBA-focused pipeline behavior."""

from __future__ import annotations


# 2026 NBA postseason field (kept explicit for stable, auditable behavior).
NBA_PLAYOFF_TEAMS = {
    "ATL",
    "BOS",
    "BRK",
    "CHI",
    "CLE",
    "DEN",
    "GSW",
    "HOU",
    "IND",
    "LAC",
    "LAL",
    "MEM",
    "MIA",
    "MIL",
    "MIN",
    "NOP",
    "NYK",
    "OKC",
    "ORL",
    "PHI",
    "PHX",
    "SAC",
    "DAL",
}


def norm_team_abbr(team: object) -> str:
    raw = str(team or "").strip().upper()
    if not raw:
        return ""
    return raw.split("/")[0].strip()


def is_nba_playoff_team(team: object) -> bool:
    return norm_team_abbr(team) in NBA_PLAYOFF_TEAMS
