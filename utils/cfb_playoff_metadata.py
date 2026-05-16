"""
College Football Playoff + bowl metadata (CFB postseason).

Mirrors ``utils.cbb_tourney_metadata`` for March Madness: seeds, round labels,
and helpers for step6 ranking + combined slate enrichment.

Edit ``CFB_CFP_2026`` each season when the 12-team bracket is set.
Keys are canonical team abbreviations (PrizePicks / ESPN style).
"""

from __future__ import annotations

from typing import Optional, Tuple

# PrizePicks / slate abbr → canonical key in CFB_CFP_2026
CFB_TEAM_ALIASES: dict[str, str] = {
    "IU": "IND",
    "INDIANA": "IND",
    "OHST": "OSU",
    "OHIO ST": "OSU",
    "OHIO STATE": "OSU",
    "UGA": "UGA",
    "GA": "UGA",
    "GEORGIA": "UGA",
    "TXTECH": "TTU",
    "TEXAS TECH": "TTU",
    "ORE": "ORE",
    "OREGON": "ORE",
    "MISS": "MISS",
    "OLE MISS": "MISS",
    "OLEMISS": "MISS",
    "TAMU": "TAMU",
    "TA&M": "TAMU",
    "TXAM": "TAMU",
    "A&M": "TAMU",
    "TEXAS A&M": "TAMU",
    "OU": "OU",
    "OKLA": "OU",
    "OKLAHOMA": "OU",
    "ALA": "ALA",
    "BAMA": "ALA",
    "ALABAMA": "ALA",
    "MIA": "MIA",
    "MIAMI": "MIA",
    "MIAMI FL": "MIA",
    "TUL": "TUL",
    "TULANE": "TUL",
    "JMU": "JMU",
    "JAMES MADISON": "JMU",
}

# 2025–26 College Football Playoff (12-team field; Jan 2026 championship)
# Values: (cfp_seed: int|"", round_label: str)
# round_label: CFP_BYE | CFP_FIRST | CFP_QUARTER | CFP_SEMI | CFP_CHAMP
CFB_CFP_2026: dict[str, Tuple[int | str, str]] = {
    "IND": (1, "CFP_BYE"),
    "OSU": (2, "CFP_BYE"),
    "UGA": (3, "CFP_BYE"),
    "TTU": (4, "CFP_BYE"),
    "ORE": (5, "CFP_QUARTER"),
    "MISS": (6, "CFP_SEMI"),
    "TAMU": (7, "CFP_FIRST"),
    "OU": (8, "CFP_FIRST"),
    "ALA": (9, "CFP_QUARTER"),
    "MIA": (10, "CFP_CHAMP"),
    "TUL": (11, "CFP_FIRST"),
    "JMU": (12, "CFP_FIRST"),
}

# AP Top 25 snapshot (late 2025 season) — optional display / context
CFB_AP_TOP25_2026: dict[str, int] = {
    "IND": 1,
    "OSU": 2,
    "UGA": 3,
    "ORE": 4,
    "TTU": 5,
    "MISS": 6,
    "TAMU": 7,
    "ALA": 8,
    "MIA": 9,
    "OU": 10,
}

# NY6 / major bowls outside the CFP bracket (extend as needed)
CFB_NY6_BOWL_2026: dict[str, str] = {
    # "USC": "ROSE",
}

# Step6 score dampeners (playoff game script / tighter defenses)
CFB_PLAYOFF_YARD_SCORE_MULT = 0.90
CFB_PLAYOFF_TD_SCORE_MULT = 0.92
CFB_PLAYOFF_CHAMP_EXTRA_MULT = 0.95


def norm_cfb_team_abbr(team: object) -> str:
    raw = str(team or "").strip().upper()
    if not raw:
        return ""
    raw = raw.split("/")[0].strip()
    return CFB_TEAM_ALIASES.get(raw, raw)


def cfb_playoff_info(team: object) -> Tuple[int | str, str]:
    """Return (seed, round_label) or ('', '') if not in the CFP bracket."""
    abbr = norm_cfb_team_abbr(team)
    if not abbr:
        return "", ""
    hit = CFB_CFP_2026.get(abbr)
    if hit:
        return hit
    return "", ""


def cfb_row_in_playoff(team_val: object, opp_val: object) -> bool:
    ta, oa = norm_cfb_team_abbr(team_val), norm_cfb_team_abbr(opp_val)
    return (bool(ta) and ta in CFB_CFP_2026) or (bool(oa) and oa in CFB_CFP_2026)


def cfb_row_in_ny6_bowl(team_val: object, opp_val: object) -> bool:
    ta, oa = norm_cfb_team_abbr(team_val), norm_cfb_team_abbr(opp_val)
    return (bool(ta) and ta in CFB_NY6_BOWL_2026) or (bool(oa) and oa in CFB_NY6_BOWL_2026)


def cfb_playoff_round_for_row(team_val: object, opp_val: object) -> str:
    """Latest / highest round when either side is in the CFP bracket."""
    order = ("CFP_FIRST", "CFP_QUARTER", "CFP_SEMI", "CFP_CHAMP", "CFP_BYE")
    rounds: list[str] = []
    for side in (team_val, opp_val):
        _, rnd = cfb_playoff_info(side)
        if rnd:
            rounds.append(rnd)
    if not rounds:
        return ""
    return max(rounds, key=lambda r: order.index(r) if r in order else -1)


def cfb_is_championship_row(team_val: object, opp_val: object) -> bool:
    return cfb_playoff_round_for_row(team_val, opp_val) == "CFP_CHAMP"
