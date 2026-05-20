#!/usr/bin/env python3
"""Backfill ticket_id on tickets_latest.json legs (no full rebuild)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def backfill(path: Path) -> tuple[int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    date_str = str(data.get("date") or "")[:10]
    n_legs = 0
    n_tid = 0
    for grp in data.get("groups") or []:
        gn = str(grp.get("group_name") or "Ticket")
        gn_safe = re.sub(r"[|]+", "_", gn.strip())[:80]
        for ti, ticket in enumerate(grp.get("tickets") or [], start=1):
            if not isinstance(ticket, dict):
                continue
            tid = ticket.get("ticket_id") or f"{date_str}|{gn_safe}|{ti}"
            ticket["ticket_id"] = tid
            for leg in ticket.get("legs") or []:
                if not isinstance(leg, dict):
                    continue
                n_legs += 1
                leg["ticket_id"] = tid
                n_tid += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return n_tid, n_legs


def main() -> int:
    targets = [
        _REPO / "ui_runner" / "templates" / "tickets_latest.json",
        _REPO / "ui_runner" / "templates" / "shadow_tickets_latest.json",
        _REPO / "mobile" / "www" / "tickets_latest.json",
    ]
    for p in targets:
        if p.is_file():
            n_tid, n_legs = backfill(p)
            print(f"{p.name}: {n_tid}/{n_legs} legs with ticket_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
