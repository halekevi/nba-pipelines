"""Monthly P&L rollup for /income from grade_history daily rows."""

from __future__ import annotations

from typing import Any


def month_key_from_date(date_str: str) -> str:
    d = str(date_str or "").strip()[:10]
    if len(d) < 7:
        return ""
    return d[:7]


def aggregate_monthly_from_daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll up daily income rows into YYYY-MM buckets (newest month first)."""
    buckets: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        month = month_key_from_date(str(row.get("date") or ""))
        if not month:
            continue
        bucket = buckets.setdefault(
            month,
            {"tickets": 0.0, "decided": 0.0, "paid": 0.0, "net_dollars": 0.0},
        )
        bucket["tickets"] += float(row.get("tickets") or 0)
        bucket["decided"] += float(row.get("decided") or 0)
        bucket["paid"] += float(row.get("paid") or 0)
        bucket["net_dollars"] += float(row.get("net_dollars") or 0)

    out: list[dict[str, Any]] = []
    for month in sorted(buckets.keys()):
        b = buckets[month]
        tickets = int(b["tickets"])
        decided = int(b["decided"])
        paid = int(b["paid"])
        net_dollars = round(float(b["net_dollars"]), 2)
        win_rate = (paid / decided) if decided > 0 else None
        roi_pct = round((net_dollars / (tickets * 10.0)) * 100.0, 2) if tickets > 0 else 0.0
        out.append(
            {
                "month": month,
                "tickets": tickets,
                "decided": decided,
                "paid": paid,
                "win_rate": win_rate,
                "net_dollars": net_dollars,
                "roi_pct": roi_pct,
            }
        )
    out.reverse()
    return out
