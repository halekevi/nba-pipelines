"""
Derive HIT/MISS/PUSH + margin from numeric actual, line, and OVER/UNDER.

Used by step_archive (persist correct outcomes) and the Grades API / bundle export
(fix stale VOID in Prop Evaluation without re-running every sport grader).
"""
from __future__ import annotations

import math
from typing import Any


def _scalar_is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip()
        if not s or s.lower() in ("nan", "none", "nat"):
            return True
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def reconcile_result_margin_from_box_score(
    actual_raw: Any,
    line_raw: Any,
    side: Any,
    result_in: Any,
    margin_raw: Any,
) -> tuple[Any, Any]:
    """
    When actual and line are numeric and side is OVER or UNDER, return HIT/MISS/PUSH and margin.
    Otherwise return (result_in, margin_raw) unchanged.
    """
    if _scalar_is_missing(actual_raw) or _scalar_is_missing(line_raw):
        return result_in, margin_raw
    try:
        a = float(actual_raw)
        ln = float(line_raw)
    except (TypeError, ValueError):
        return result_in, margin_raw
    s = str(side or "").strip().upper()
    if s not in ("OVER", "UNDER"):
        return result_in, margin_raw
    if abs(a - ln) < 1e-9:
        return "PUSH", 0.0
    if s == "OVER":
        return ("HIT" if a > ln else "MISS"), round(a - ln, 2)
    return ("HIT" if a < ln else "MISS"), round(ln - a, 2)


def reconcile_props_history_dict(row: dict[str, Any]) -> dict[str, Any]:
    """
    Apply box-score reconciliation to one props_history / API row (mutates a shallow copy).
    Expects keys: actual_value, line, direction, result, margin (optional).
    """
    out = dict(row)
    res = str(out.get("result") or "").strip().upper()
    if res == "WIN":
        res = "HIT"
    elif res == "LOSS":
        res = "MISS"
    d = str(out.get("direction") or "").strip().upper()
    if d not in ("OVER", "UNDER"):
        return out
    new_res, new_mg = reconcile_result_margin_from_box_score(
        out.get("actual_value"),
        out.get("line"),
        d,
        res,
        out.get("margin"),
    )
    out["result"] = new_res
    out["margin"] = new_mg
    return out
