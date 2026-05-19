#!/usr/bin/env python3
"""Build lookup sets from tickets_latest / shadow_tickets_latest JSON leg lists."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _norm_player(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _norm_prop(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _norm_dir(s: object) -> str:
    d = str(s or "").strip().upper()
    if d in ("UNDER", "U", "LOWER"):
        return "UNDER"
    return "OVER"


def _norm_line(s: object) -> str:
    try:
        return f"{float(s):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(s or "").strip()


def leg_key_from_record(rec: dict[str, Any], *, sport_default: str = "") -> tuple[str, ...]:
    sp = str(rec.get("sport") or sport_default or "").strip().upper()
    player = _norm_player(rec.get("player") or rec.get("Player"))
    prop = _norm_prop(rec.get("prop_type") or rec.get("prop") or rec.get("Prop Type") or rec.get("Prop"))
    direction = _norm_dir(rec.get("direction") or rec.get("dir") or rec.get("Direction"))
    line = _norm_line(rec.get("line") or rec.get("Line"))
    pick = str(rec.get("pick_type") or rec.get("pick") or rec.get("Pick Type") or "Standard").strip().lower()
    pid = str(rec.get("pp_projection_id") or rec.get("projection_id") or "").strip()
    if pid:
        return ("id", pid)
    return (sp, player, prop, line, direction, pick)


def load_ticket_leg_keys(path: Path) -> set[tuple[str, ...]]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    keys: set[tuple[str, ...]] = set()
    groups = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(groups, list):
        return keys
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        sport_default = str(grp.get("sport") or "").strip().upper()
        for ticket in grp.get("tickets") or []:
            if not isinstance(ticket, dict):
                continue
            for leg in ticket.get("legs") or []:
                if isinstance(leg, dict):
                    keys.add(leg_key_from_record(leg, sport_default=sport_default))
    return keys


def prop_matches_ticket_keys(row: dict[str, Any], sport: str, keys: set[tuple[str, ...]]) -> bool:
    if not keys:
        return False
    k = leg_key_from_record(row, sport_default=sport)
    if k in keys:
        return True
    if k[0] == "id":
        return False
    sp, player, prop, line, direction = k[0], k[1], k[2], k[3], k[4]
    for kk in keys:
        if kk[0] == "id":
            continue
        if kk[:5] == (sp, player, prop, line, direction):
            return True
    return False
