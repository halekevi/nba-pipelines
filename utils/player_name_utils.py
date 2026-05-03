"""
Shared player-name normalization for actuals CSV, slate grader joins, ticket eval,
and injury sidecars.

Folds Unicode diacritics to ASCII and strips Jr./Sr./II–IV suffixes so slate, ESPN,
and injury rows agree on lookup keys.
"""

from __future__ import annotations

import re
import unicodedata

_SUFFIXES = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", re.IGNORECASE)


def normalize_player_name(name: str) -> str:
    """
    Normalize a display name for joins (actuals, slate, injuries):
    1. NFD + ASCII (Vučević → Vucevic, Dončić → Doncic)
    2. Strip Jr./Sr./II/III/IV/V suffixes (Kelly Oubre Jr. → Kelly Oubre)
    3. Trim whitespace
    """
    if name is None:
        return ""
    if isinstance(name, float) and name != name:  # NaN
        return ""
    s = unicodedata.normalize("NFD", str(name).strip())
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.replace(".", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = _SUFFIXES.sub("", s).strip()
    return s
