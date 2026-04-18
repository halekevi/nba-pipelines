#!/usr/bin/env python3
"""Merge Railway payout_log_hand.csv into local copy (dedupe by full row tuple)."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

FIELDNAMES = [
    "date",
    "group_name",
    "n_legs",
    "pick_types",
    "lines",
    "standard_lines",
    "actual_payout_multiplier",
    "slip_type",
    "result",
]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _key(r: dict) -> tuple[str, ...]:
    return tuple(str(r.get(h, "") or "").strip() for h in FIELDNAMES)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", required=True, type=Path, help="Local payout_log_hand.csv to update")
    ap.add_argument("--remote", required=True, type=Path, help="Downloaded CSV from Railway export")
    args = ap.parse_args()
    seen: set[tuple[str, ...]] = set()
    merged: list[dict[str, str]] = []
    for path in (args.local, args.remote):
        for r in _read_rows(path):
            k = _key(r)
            if k in seen:
                continue
            seen.add(k)
            row = {h: str(r.get(h, "") or "").strip() for h in FIELDNAMES}
            merged.append(row)
    args.local.parent.mkdir(parents=True, exist_ok=True)
    with args.local.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(merged)
    print(f"Wrote {len(merged)} rows -> {args.local}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
