#!/usr/bin/env python3
"""Quick ticket-composition audit for combined_slate_tickets JSON."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def iter_ticket_legs(data: dict, *, track: str | None = None) -> list[dict]:
    legs: list[dict] = []
    for g in data.get("groups", []):
        for t in g.get("tickets", []):
            if track:
                t_track = str(t.get("ticket_track") or data.get("ticket_track") or "").lower()
                if t_track != track.lower():
                    continue
            legs.extend(t.get("legs", []))
    if not legs and not track:
        for t in data.get("tickets", []):
            legs.extend(t.get("legs", []))
    if track:
        legs = [l for l in legs if str(l.get("ticket_track") or "").lower() == track.lower()]
    return legs


def summarize(path: Path, track: str | None = None) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    file_track = str(data.get("ticket_track") or "")
    tickets_n = 0
    for g in data.get("groups", []):
        for t in g.get("tickets", []):
            if track:
                t_track = str(t.get("ticket_track") or file_track or "").lower()
                if t_track != track.lower():
                    continue
            tickets_n += 1
    if not tickets_n and not track:
        tickets_n = len(data.get("tickets", []))

    legs = iter_ticket_legs(data, track=track)
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

    label = track or file_track or "all"
    print(f"\nFile: {path}  track={label}")
    print(f"Tickets: {tickets_n}")
    print(f"Total legs: {len(legs)}")
    print(f"Pick type: {dict(pt)}")
    print(f"Sport: {dict(sport)}")
    print(f"Demon OVER legs: {dem_over}")
    print(f"HOT streak legs: {hot}")
    print(f"COLD streak legs: {cold}")
    if legs:
        print(f"HOT pct: {100 * hot / len(legs):.1f}%")
    group_hot = sum(int(g.get("hot_legs") or 0) for g in data.get("groups", []))
    group_cold = sum(int(g.get("cold_legs") or 0) for g in data.get("groups", []))
    if group_hot or group_cold or data.get("hot_legs") is not None:
        print(
            f"Group summary hot/cold: {group_hot}/{group_cold} "
            f"(payload: {data.get('hot_legs', '?')}/{data.get('cold_legs', '?')})"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate combined slate ticket composition")
    ap.add_argument(
        "path",
        nargs="?",
        default="ui_runner/data/combined_slate_tickets_latest.json",
        help="Path to combined_slate_tickets JSON",
    )
    ap.add_argument(
        "--track",
        choices=("graded_main", "high_leg_hr", "main", "win_rate"),
        help="Filter to ticket_track (main = graded_main)",
    )
    ap.add_argument(
        "--high-leg",
        action="store_true",
        help="Also summarize combined_slate_tickets_high_leg_<date>.json sibling if present",
    )
    args = ap.parse_args()
    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    track = args.track
    if track == "main":
        track = "graded_main"
    if track == "win_rate":
        track = "high_leg_hr"

    summarize(path, track=track)

    if args.high_leg or track == "high_leg_hr":
        stem = path.stem
        if stem.startswith("combined_slate_tickets_"):
            date_part = stem.replace("combined_slate_tickets_", "")
            hi = path.parent / f"combined_slate_tickets_high_leg_{date_part}.json"
            if hi.is_file():
                summarize(hi, track="high_leg_hr")


if __name__ == "__main__":
    main()
