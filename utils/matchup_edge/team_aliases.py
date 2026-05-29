"""Team abbreviation normalization for matchup-edge defense lookups."""

from __future__ import annotations


def cbb_slate_to_defense_key(abbr: str) -> str:
    """PrizePicks team abbr (DUKE) -> Sports Reference name (Duke) for CBB def CSV."""
    try:
        from Sports.CBB.scripts.cbb_team_map import ABBR_TO_SR
    except ImportError:
        return str(abbr or "").strip()
    a = str(abbr or "").strip().upper()
    return ABBR_TO_SR.get(a, str(abbr or "").strip())


def cbb_defense_alias_keys() -> dict[str, str]:
    """Map PP abbr -> sr_name for every known CBB team."""
    try:
        from Sports.CBB.scripts.cbb_team_map import ABBR_TO_SR
    except ImportError:
        return {}
    return {k.upper(): v for k, v in ABBR_TO_SR.items()}


MLB_DISPLAY_NAMES: dict[str, str] = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "CWS": "Chicago White Sox",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC": "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "OAK": "Athletics",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres",
    "SDP": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants",
    "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
    "AZ": "Arizona Diamondbacks",
}


def mlb_display_name(abbr: str) -> str:
    a = str(abbr or "").strip().upper()
    return MLB_DISPLAY_NAMES.get(a, a or str(abbr or "").strip())
