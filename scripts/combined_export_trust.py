#!/usr/bin/env python3
"""Classify combined_slate_tickets JSON exports as live vs backfill."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# STEP D outage rebuild batch (06-26 UTC evening). Generic checks below catch repeats.
KNOWN_BACKFILL_DATES: frozenset[str] = frozenset(
    {"2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"}
)

# Manual rebuild cluster on 06-26 was ~22:xx UTC; live scheduled runs are earlier same day.
_LATE_NIGHT_UTC_HOUR = 22


def _parse_generated_at(raw: str) -> datetime | None:
    s = str(raw or "").strip()
    if len(s) < 19:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def classify_combined_export_payload(payload: dict[str, Any] | None, date_str: str) -> tuple[str, str | None]:
    """
    Return (trust, reason) where trust is 'live' or 'backfill'.

    Backfill if:
      - filename date is in KNOWN_BACKFILL_DATES (incident list), or
      - embedded payload date != filename date, or
      - generated_at calendar date != filename date, or
      - same-day generated_at is late-night UTC (manual rebuild pattern).
    """
    if date_str in KNOWN_BACKFILL_DATES:
        return "backfill", "known_backfill_date"

    if not payload:
        return "live", None

    embedded = str(payload.get("date") or "").strip()[:10]
    if embedded and embedded != date_str:
        return "backfill", f"embedded_date={embedded}"

    gen_raw = str(payload.get("generated_at") or "").strip()
    if len(gen_raw) >= 10:
        gen_date = gen_raw[:10]
        if gen_date != date_str:
            return "backfill", f"generated_at_date={gen_date}"
        gen_dt = _parse_generated_at(gen_raw)
        if gen_dt is not None and gen_dt.hour >= _LATE_NIGHT_UTC_HOUR:
            return "backfill", f"late_night_generated_at={gen_raw}"

    return "live", None


def classify_combined_export_file(path: Path, date_str: str) -> tuple[str, str | None]:
    if not path.is_file():
        return "live", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "live", None
    return classify_combined_export_payload(payload, date_str)


def day_export_trust(
    *,
    date_str: str,
    baseline_path: Path | None = None,
    shadow_path: Path | None = None,
    baseline_payload: dict[str, Any] | None = None,
    shadow_payload: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    """A day is backfill if baseline or shadow export for that slate date is backfill."""
    reasons: list[str] = []
    for label, path, payload in (
        ("baseline", baseline_path, baseline_payload),
        ("shadow", shadow_path, shadow_payload),
    ):
        if payload is not None:
            trust, reason = classify_combined_export_payload(payload, date_str)
        elif path is not None:
            trust, reason = classify_combined_export_file(path, date_str)
        else:
            continue
        if trust == "backfill":
            reasons.append(f"{label}:{reason or 'backfill'}")
    if reasons:
        return "backfill", "; ".join(reasons)
    return "live", None
