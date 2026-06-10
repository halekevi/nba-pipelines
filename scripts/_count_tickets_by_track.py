#!/usr/bin/env python3
"""Quick count: graded main vs high-leg-HR tickets + HR buckets."""
import json
import statistics as st
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATE = "2026-06-09"

paths = {
    "graded_main_dated": REPO / f"ui_runner/data/combined_slate_tickets_{DATE}.json",
    "high_leg_hr": REPO / f"ui_runner/data/combined_slate_tickets_high_leg_{DATE}.json",
    "tickets_latest": REPO / "ui_runner/templates/tickets_latest.json",
    "winrate_latest": REPO / "ui_runner/templates/tickets_winrate_latest.json",
}


def summarize(data, label):
    if not data:
        print(f"--- {label}: (missing) ---\n")
        return 0
    groups = data.get("groups") or []
    slips = [t for g in groups for t in (g.get("tickets") or [])]
    legs = [l for g in groups for t in (g.get("tickets") or []) for l in (t.get("legs") or [])]
    track = data.get("ticket_track") or data.get("mode") or "?"
    print(f"--- {label} ---")
    print(f"  date: {data.get('date')}  generated: {str(data.get('generated_at', ''))[:19]}")
    print(f"  track: {track}")
    print(f"  groups: {len(groups)}  tickets (slips): {len(slips)}  legs: {len(legs)}")

    avg_hrs = [float(t["avg_hit_rate"]) for t in slips if t.get("avg_hit_rate") is not None]
    if avg_hrs:
        print(
            f"  slip avg_hit_rate: min={min(avg_hrs):.1%} "
            f"median={st.median(avg_hrs):.1%} max={max(avg_hrs):.1%}"
        )

    all_legs_high = any_leg_low = 0
    for t in slips:
        leg_hrs = [
            float(l["hit_rate"]) for l in (t.get("legs") or []) if l.get("hit_rate") is not None
        ]
        if not leg_hrs:
            continue
        if all(h >= 0.72 for h in leg_hrs):
            all_legs_high += 1
        if any(h < 0.55 for h in leg_hrs):
            any_leg_low += 1
    print(f"  slips with ALL legs >=72% HR: {all_legs_high}")
    print(f"  slips with ANY leg <55% HR: {any_leg_low}")

    leg_hrs = [float(l["hit_rate"]) for l in legs if l.get("hit_rate") is not None]
    if leg_hrs:
        print(
            f"  legs: <55%={sum(h < 0.55 for h in leg_hrs)}  "
            f"55-72%={sum(0.55 <= h < 0.72 for h in leg_hrs)}  "
            f">=72%={sum(h >= 0.72 for h in leg_hrs)}"
        )

    n_legs = Counter(len(t.get("legs") or []) for t in slips)
    print(f"  tickets by # legs: {dict(sorted(n_legs.items()))}")
    print()
    return len(slips)


def main():
    loaded = {}
    for k, p in paths.items():
        loaded[k] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def leg_size_counts(data, label):
        if not data:
            print(f"--- {label}: (missing) ---\n")
            return None
        slips = [t for g in data.get("groups", []) for t in g.get("tickets", [])]
        by_n = Counter(len(t.get("legs") or []) for t in slips)
        low = sum(1 for t in slips if 2 <= len(t.get("legs") or []) <= 4)
        high = sum(1 for t in slips if 5 <= len(t.get("legs") or []) <= 6)
        track = data.get("ticket_track") or data.get("mode") or label
        print(f"--- {label} (track={track}) ---")
        print(f"  total tickets: {len(slips)}  groups: {len(data.get('groups') or [])}")
        print(f"  LOW-leg parlays (2-4 legs): {low}")
        print(f"  HIGH-leg parlays (5-6 legs): {high}")
        print(f"  breakdown: {dict(sorted(by_n.items()))}")
        print()
        return len(slips), low, high

    print(f"Slate date: {DATE}\n")
    r_main = leg_size_counts(loaded["graded_main_dated"], "Graded main")
    r_hr = leg_size_counts(loaded["high_leg_hr"], "Win-rate panel (separate track)")
    if r_main:
        total, low, high = r_main
        print("=== GRADED MAIN ===")
        print(f"  Total tickets: {total}")
        print(f"  Low-leg (2-4): {low} ({100 * low / total:.1f}%)")
        print(f"  High-leg (5-6): {high} ({100 * high / total:.1f}%)")
    if r_main and r_hr:
        print(f"\n  All graded main + win-rate panel: {r_main[0] + r_hr[0]} tickets")


if __name__ == "__main__":
    main()
