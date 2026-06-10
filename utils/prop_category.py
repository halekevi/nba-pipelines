"""Map display prop labels to top-3 / strat category ids (pts, ast, sog, …)."""

from __future__ import annotations

import re
import unicodedata

NBA_PROP_CAT: dict[str, str] = {
    "points": "pts",
    "pts": "pts",
    "rebounds": "reb",
    "reb": "reb",
    "offensiverebounds": "reb",
    "defensiverebounds": "reb",
    "assists": "ast",
    "ast": "ast",
    "steals": "stl",
    "stl": "stl",
    "blocks": "blk",
    "blk": "blk",
    "3ptmade": "fg3m",
    "3ptmmade": "fg3m",
    "3pt": "fg3m",
    "fg3m": "fg3m",
    "threes": "fg3m",
    "pts+rebs+asts": "pra",
    "pts+rebs": "pra",
    "pts+asts": "pra",
    "rebs+asts": "pra",
    "fantasyscore": "pra",
}

WNBA_PROP_CAT = dict(NBA_PROP_CAT)

NHL_PROP_CAT: dict[str, str] = {
    "goals": "goals",
    "goal": "goals",
    "assists": "assists",
    "assist": "assists",
    "points": "points",
    "pts": "points",
    "powerplaypoints": "points",
    "shots": "shots",
    "shotsongoal": "shots",
    "sog": "shots",
    "shots_on_goal": "shots",
}

MLB_PROP_CAT: dict[str, str] = {
    "hits": "hits",
    "hit": "hits",
    "runs": "hits",
    "run": "hits",
    "totalbases": "total_bases",
    "total_bases": "total_bases",
    "homeruns": "home_runs",
    "home_runs": "home_runs",
    "hr": "home_runs",
    "rbi": "rbi",
    "rbis": "rbi",
}

_SPORT_MAPS: dict[str, dict[str, str]] = {
    "NBA": NBA_PROP_CAT,
    "NBA1H": NBA_PROP_CAT,
    "NBA1Q": NBA_PROP_CAT,
    "WNBA": WNBA_PROP_CAT,
    "NHL": NHL_PROP_CAT,
    "MLB": MLB_PROP_CAT,
}


def _norm_sport(sport: object) -> str:
    return str(sport or "").strip().upper()


def _norm_prop_key(prop: object) -> str:
    s = str(prop or "").strip().lower()
    s = re.sub(r"[^a-z0-9+]+", " ", s).strip()
    s = s.replace(" ", "")
    s = s.replace("threepoint", "3pt").replace("3ptmade", "3pt")
    return s


def prop_to_category(sport: object, prop: object, *, prop_raw: object = "") -> str:
    sp = _norm_sport(sport)
    key = _norm_prop_key(prop_raw or prop)
    if "+" in key:
        for part in key.split("+"):
            if part in ("pts", "rebs", "asts", "reb", "ast"):
                return "pra"
    m = _SPORT_MAPS.get(sp, {})
    if key in m:
        return m[key]
    for pat, cat in m.items():
        if pat in key or key in pat:
            return cat
    return m.get(key, "")
