"""
Shared player-name folding for MLB grader, ticket eval, and NBA/CBB slate grader.

NFKD + strip combining marks (Jesús → jesus), dots → spaces, Jr./Sr./II–V dropped.
Keeps actuals ↔ slate ↔ ticket JSON keys aligned.
"""
from __future__ import annotations

import re
import unicodedata


def fold_player_name(s) -> str:
    if s is None:
        return ""
    if isinstance(s, float) and s != s:  # NaN
        return ""
    t = unicodedata.normalize("NFKD", str(s).strip())
    t = "".join(c for c in t if not unicodedata.combining(c))
    p = t.lower().replace(".", " ")
    p = re.sub(r"\s+", " ", p)
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    parts = [x for x in p.split(" ") if x and x not in suffixes]
    return " ".join(parts)
