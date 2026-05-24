"""
Shared leg HIT/MISS rules for ticket eval, combined_ticket_grader, and slate_grader.

PrizePicks: exact line is a push (VOID), not a hit. OVER requires actual > line;
UNDER requires actual < line. Legs within NEAR_LINE_EPS of the line are flagged
NEAR_LINE for manual review instead of silently grading.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

NEAR_LINE_EPS = 0.05
PUSH_EPS = 1e-9

# prop_norm / canonical keys -> component stat labels in actuals CSV (PrizePicks / ESPN)
NBA_COMBO_COMPONENTS: dict[str, tuple[str, ...]] = {
    "pts+rebs+asts": ("Points", "Rebounds", "Assists"),
    "pra": ("Points", "Rebounds", "Assists"),
    "pts+rebs": ("Points", "Rebounds"),
    "pr": ("Points", "Rebounds"),
    "pts+asts": ("Points", "Assists"),
    "pa": ("Points", "Assists"),
    "rebs+asts": ("Rebounds", "Assists"),
    "ra": ("Rebounds", "Assists"),
    "blks+stls": ("Blocked Shots", "Steals"),
    "bs": ("Blocked Shots", "Steals"),
}

_COMBO_ALIASES: dict[str, str] = {
    "pts+rebs+asts": "pts+rebs+asts",
    "points+rebounds+assists": "pts+rebs+asts",
    "pts+rebs": "pts+rebs",
    "points+rebounds": "pts+rebs",
    "pts+asts": "pts+asts",
    "points+assists": "pts+asts",
    "rebs+asts": "rebs+asts",
    "rebounds+assists": "rebs+asts",
    "blks+stls": "blks+stls",
    "blocks+steals": "blks+stls",
    "pr": "pts+rebs",
    "pa": "pts+asts",
    "ra": "rebs+asts",
    "pra": "pts+rebs+asts",
    "bs": "blks+stls",
}


def normalize_combo_prop_key(prop: object) -> str:
    """Fold display / norm prop labels to a combo lookup key."""
    if prop is None:
        return ""
    s = str(prop).strip().lower()
    s = re.sub(r"\s+", "", s)
    s = s.replace("(combo)", "")
    if s in _COMBO_ALIASES:
        return _COMBO_ALIASES[s]
    if "pts+reb+ast" in s or s == "pra":
        return "pts+rebs+asts"
    if "pts+reb" in s or s == "pr":
        return "pts+rebs"
    if "pts+ast" in s or s == "pa":
        return "pts+asts"
    if "reb+ast" in s or s == "ra":
        return "rebs+asts"
    if "blk+stl" in s or "blks+stls" in s or s == "bs":
        return "blks+stls"
    return s


def is_nba_combo_prop(prop: object) -> bool:
    return normalize_combo_prop_key(prop) in NBA_COMBO_COMPONENTS


def sum_nba_combo_from_actuals_df(
    act: pd.DataFrame,
    player: str,
    team: str,
    prop: object,
) -> float | None:
    """
    Sum final box-score component rows for one player (same team) — no intermediate rounding.
    Returns None when any component stat is missing.
    """
    if act is None or act.empty:
        return None
    key = normalize_combo_prop_key(prop)
    comps = NBA_COMBO_COMPONENTS.get(key)
    if not comps:
        return None
    if not {"player", "team", "prop_type", "actual"}.issubset(act.columns):
        return None

    pl = str(player or "").strip()
    tm = str(team or "").strip()
    if not pl:
        return None

    base = act
    if tm:
        base = base.loc[base["team"].astype(str).str.strip().eq(tm)]
    base = base.loc[base["player"].astype(str).str.strip().eq(pl)]

    total = 0.0
    for cp in comps:
        sel = base.loc[base["prop_type"].astype(str).str.strip().eq(cp), "actual"]
        if sel.empty:
            return None
        v = pd.to_numeric(sel.iloc[0], errors="coerce")
        if pd.isna(v):
            return None
        total += float(v)
    return total


def compare_leg_to_line(
    actual: float | None,
    line: float | None,
    direction: str,
) -> tuple[str, float | None, bool]:
    """
    Grade one leg numerically.

    Returns (result, margin, near_line_flag).
    result ∈ {HIT, MISS, PUSH, NEAR_LINE, NO_ACTUAL, UNKNOWN_DIR}.
    """
    if actual is None or (isinstance(actual, float) and (math.isnan(actual) or math.isinf(actual))):
        return "NO_ACTUAL", None, False
    if line is None or (isinstance(line, float) and (math.isnan(line) or math.isinf(line))):
        return "NO_ACTUAL", None, False

    a = float(actual)
    ln = float(line)
    d = str(direction or "").strip().upper()
    margin = a - ln

    if abs(margin) <= PUSH_EPS:
        return "PUSH", 0.0, False

    near = abs(margin) < NEAR_LINE_EPS

    if d == "OVER":
        if near:
            _log.warning(
                "NEAR_LINE leg: actual=%s line=%s direction=OVER margin=%s",
                a,
                ln,
                margin,
            )
            return "NEAR_LINE", margin, True
        if a > ln:
            return "HIT", margin, False
        return "MISS", margin, False

    if d == "UNDER":
        if near:
            _log.warning(
                "NEAR_LINE leg: actual=%s line=%s direction=UNDER margin=%s",
                a,
                ln,
                margin,
            )
            return "NEAR_LINE", margin, True
        if a < ln:
            return "HIT", margin, False
        return "MISS", margin, False

    return "UNKNOWN_DIR", margin, False


def grade_leg_strict(dir_: str, line: float | None, actual: float | None) -> str:
    """Ticket-grader style single-string result (includes PUSH / NO_ACTUAL)."""
    r, _, _ = compare_leg_to_line(actual, line, dir_)
    if r == "NO_ACTUAL":
        return "NO_ACTUAL"
    if r == "UNKNOWN_DIR":
        return "UNKNOWN_DIR"
    if r == "PUSH":
        return "PUSH"
    return r


def leg_grade_for_ticket_eval(
    actual: float | None,
    line: float | None,
    direction: str,
    grade_col: str,
    void_note: str = "",
) -> str:
    """
    Prefer numeric grading when actual+line exist (build_ticket_eval._leg_grade).
    Falls back to stored grade column when numeric inputs are unavailable.
    """
    r, _, _ = compare_leg_to_line(actual, line, direction)
    d = str(direction or "").strip().upper()
    if d in ("OVER", "UNDER") and r not in ("NO_ACTUAL", "UNKNOWN_DIR"):
        if r == "PUSH":
            return "VOID"
        return r

    g = (grade_col or "").strip().upper()
    if g in ("HIT", "WIN", "W", "1", "TRUE", "YES"):
        return "HIT"
    if g in ("MISS", "LOSS", "L", "0", "FALSE", "NO"):
        return "MISS"
    if g in ("VOID", "PUSH", "N/A", "NA"):
        vn = str(void_note or "").strip().upper()
        if actual is None or line is None:
            if not vn or vn in ("NO_ACTUAL", "MISSING_ACTUAL", "PENDING", "TBD", "UNKNOWN"):
                return "UNGRADED"
        else:
            try:
                a = float(actual)
                ln = float(line)
                if not (math.isnan(a) or math.isnan(ln)):
                    if not vn or vn in ("NO_ACTUAL", "MISSING_ACTUAL", "PENDING", "TBD", "UNKNOWN"):
                        return "UNGRADED"
            except (TypeError, ValueError):
                pass
        return "VOID"
    return "UNGRADED"


def slate_grade_row(actual: float | None, row: dict[str, Any] | pd.Series) -> tuple[str, str | None, float | None]:
    """Return (result, void_reason, margin) for slate_grader.apply_actuals."""
    act = pd.to_numeric(actual, errors="coerce")
    if pd.isna(act):
        return "VOID", "NO_ACTUAL", np.nan

    line = None
    for k in ("line", "Line", "line_score", "LINE", "main_line"):
        if k in row.index if hasattr(row, "index") else k in row:
            v = row.get(k) if isinstance(row, dict) else row.get(k)
            try:
                line = float(v)
                if not math.isnan(line):
                    break
            except (TypeError, ValueError):
                continue
    if line is None or math.isnan(line):
        return "VOID", "NO_LINE", np.nan

    direction = "OVER"
    for k in ("bet_direction", "final_bet_direction", "direction", "Direction", "over_under"):
        if k in row.index if hasattr(row, "index") else k in row:
            s = str(row.get(k) if isinstance(row, dict) else row[k] or "").strip().upper()
            if s in ("OVER", "UNDER"):
                direction = s
                break

    r, margin, _ = compare_leg_to_line(float(act), line, direction)
    if r == "NO_ACTUAL":
        return "VOID", "NO_ACTUAL", np.nan
    if r == "UNKNOWN_DIR":
        return "VOID", "UNKNOWN_DIR", np.nan
    if r == "PUSH":
        return "PUSH", None, 0.0
    if r == "NEAR_LINE":
        return "NEAR_LINE", "NEAR_LINE", margin
    m = round(float(margin), 4) if margin is not None else np.nan
    if direction == "UNDER" and margin is not None:
        m = round(line - float(act), 4)
    return r, None, m


class LegGradeAudit:
    """Track flips when re-grading with stricter comparison rules."""

    def __init__(self) -> None:
        self.regraded = 0
        self.hit_to_miss = 0
        self.miss_to_hit = 0
        self.near_line = 0

    def record(self, old: str, new: str) -> None:
        o = str(old or "").strip().upper()
        n = str(new or "").strip().upper()
        if o == n:
            return
        if n == "NEAR_LINE":
            self.near_line += 1
            self.regraded += 1
            return
        if o in ("HIT", "MISS") and n in ("HIT", "MISS"):
            self.regraded += 1
            if o == "HIT" and n == "MISS":
                self.hit_to_miss += 1
            elif o == "MISS" and n == "HIT":
                self.miss_to_hit += 1

    def print_summary(self, prefix: str = "") -> None:
        tag = f"{prefix} " if prefix else ""
        print(
            f"{tag}Legs re-graded: {self.regraded}\n"
            f"{tag}Flipped HIT→MISS: {self.hit_to_miss}\n"
            f"{tag}Flipped MISS→HIT: {self.miss_to_hit}\n"
            f"{tag}Near-line flagged: {self.near_line}"
        )
