#!/usr/bin/env python3
"""Quick ticket-composition audit for combined_slate_tickets JSON."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def iter_ticket_legs(data: dict) -> list[dict]:
  legs: list[dict] = []
  for g in data.get("groups", []):
    for t in g.get("tickets", []):
      legs.extend(t.get("legs", []))
  if not legs:
    for t in data.get("tickets", []):
      legs.extend(t.get("legs", []))
  return legs


def main() -> None:
  ap = argparse.ArgumentParser(description="Validate combined slate ticket composition")
  ap.add_argument(
    "path",
    nargs="?",
    default="ui_runner/data/combined_slate_tickets_latest.json",
    help="Path to combined_slate_tickets JSON",
  )
  args = ap.parse_args()
  path = Path(args.path)
  if not path.is_file():
    # try dated file in same dir
    alt = path.parent / "combined_slate_tickets_2026-06-09.json"
    if alt.is_file():
      path = alt
    else:
      raise SystemExit(f"File not found: {path}")

  data = json.loads(path.read_text(encoding="utf-8"))
  tickets_n = sum(len(g.get("tickets", [])) for g in data.get("groups", [])) or len(
    data.get("tickets", [])
  )
  legs = iter_ticket_legs(data)

  pt = Counter(str(l.get("pick_type", "")).lower() for l in legs)
  sport = Counter(str(l.get("sport", "")).upper() for l in legs)
  dem_over = sum(
    1
    for l in legs
    if str(l.get("pick_type", "")).lower() == "demon"
    and str(l.get("direction", "")).upper() == "OVER"
  )
  hot = sum(1 for l in legs if str(l.get("l10_streak", "")).upper() == "HOT")
  cold = sum(1 for l in legs if str(l.get("l10_streak", "")).upper() == "COLD")

  print(f"File: {path}")
  print(f"Tickets: {tickets_n}")
  print(f"Total legs: {len(legs)}")
  print(f"Pick type: {dict(pt)}")
  print(f"Sport: {dict(sport)}")
  print(f"Demon OVER legs: {dem_over}")
  print(f"HOT streak legs: {hot}")
  print(f"COLD streak legs: {cold}")
  if legs:
    print(f"HOT pct: {100 * hot / len(legs):.1f}%")


if __name__ == "__main__":
  main()
