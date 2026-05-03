"""
Shared player-name folding for MLB grader, ticket eval, and NBA/CBB slate grader.

Delegates diacritic + suffix stripping to ``utils.player_name_utils.normalize_player_name``,
then lowercases, drops stray dots, and removes surname particles for long names
(e.g. Tristan da Silva vs Tristan Silva).
"""

from __future__ import annotations

import re

from utils.player_name_utils import normalize_player_name as _ascii_player_name

# Surname particles (lowercased). ESPN/box scores often keep "da/de/dos" while books list "First Last"
# only — e.g. Tristan da Silva vs Tristan Silva. Drop these only when they appear as middle token(s)
# with 3+ tokens so we do not mangle two-token names.
_NAME_PARTICLES = frozenset({"da", "de", "del", "dos", "das", "do", "di"})


def fold_player_name(s) -> str:
    if s is None:
        return ""
    if isinstance(s, float) and s != s:  # NaN
        return ""
    t = _ascii_player_name(s)
    p = t.lower().replace(".", " ")
    p = re.sub(r"\s+", " ", p)
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    parts = [x for x in p.split(" ") if x and x not in suffixes]
    if len(parts) >= 3:
        parts = [x for x in parts if x not in _NAME_PARTICLES]
    return " ".join(parts)
