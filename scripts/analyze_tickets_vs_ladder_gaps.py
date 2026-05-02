#!/usr/bin/env python3
"""
Scan tickets_latest.json for shapes that match payout-ladder gap analysis.
Uses leg pick_type + line vs standard_line (tickets omit line_distance).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Canonical web artifact (combined_slate_tickets --write-web); fallback for old layouts.
_TICKETS_PRIMARY = REPO / "ui_runner" / "templates" / "tickets_latest.json"
_TICKETS_FALLBACK = REPO / "data" / "archive" / "tickets_latest_repo_root.json"
TICKETS = _TICKETS_PRIMARY if _TICKETS_PRIMARY.exists() else _TICKETS_FALLBACK


def mix_and_deltas(legs: list) -> tuple[dict[str, int], list[float]]:
    sig = {"goblin": 0, "standard": 0, "demon": 0}
    deltas: list[float] = []
    for leg in legs or []:
        pt = str(leg.get("pick_type", "standard")).strip().lower()
        if "goblin" in pt:
            sig["goblin"] += 1
        elif "demon" in pt:
            sig["demon"] += 1
        else:
            sig["standard"] += 1
        if "goblin" in pt:
            try:
                line = float(leg.get("line") or 0.0)
                std = float(leg.get("standard_line", line) or line)
                deltas.append(round(abs(line - std), 4))
            except (TypeError, ValueError):
                deltas.append(0.0)
    deltas.sort()
    return sig, deltas


def main() -> None:
    if not TICKETS.exists():
        raise SystemExit(
            "Missing tickets JSON. Run combined_slate with --write-web, or place tickets_latest.json at:\n"
            f"  {_TICKETS_PRIMARY}\n"
            f"(optional fallback: {_TICKETS_FALLBACK})"
        )
    data = json.loads(TICKETS.read_text(encoding="utf-8"))
    groups = data.get("groups") or []

    # bucket -> list of (group, ticket_no, power/flex rates, source, mix, deltas)
    buckets: dict[str, list[dict]] = defaultdict(list)

    for g in groups:
        gname = str(g.get("group_name", ""))
        for t in g.get("tickets") or []:
            legs = t.get("legs") or []
            n = len(legs)
            if n == 0:
                continue
            po = t.get("payout") or {}
            tt = str(po.get("ticket_type", "power")).strip().lower()
            src = str(po.get("payout_source", ""))
            sig, deltas = mix_and_deltas(legs)
            gct, sct, dct = sig["goblin"], sig["standard"], sig["demon"]
            if dct > 0:
                continue  # ladder scope here: no demons

            rec = {
                "group": gname,
                "ticket_no": t.get("ticket_no"),
                "n_legs": n,
                "ticket_type": tt,
                "mix": f"{gct}G+{sct}S",
                "goblin_distances": deltas,
                "power_sweep_x": po.get("sweep_payout_x") or t.get("power_payout"),
                "power_min_x": po.get("min_payout_x") or po.get("min_guarantee"),
                "flex_first_x": t.get("flex_payout"),
                "payout_source": src,
            }

            # Gap-shaped tickets (partial goblin power)
            if tt == "power" and gct > 0 and sct > 0:
                if n == 3 and gct == 1 and sct == 2:
                    buckets["power_3L_1G+2S"].append(rec)
                elif n == 3 and gct == 2 and sct == 1:
                    buckets["power_3L_2G+1S"].append(rec)
                elif n == 4 and gct == 1 and sct == 3:
                    buckets["power_4L_1G+3S"].append(rec)

            # Flex all-goblin
            if tt == "flex" and sct == 0 and gct == n and n > 0:
                buckets[f"flex_{n}L_allG"].append(rec)

            # Flex mixed
            if tt == "flex" and gct > 0 and sct > 0:
                buckets["flex_mixed_G+S"].append(rec)

    print(f"Source: {TICKETS.name}\n")
    for name in (
        "power_3L_1G+2S",
        "power_3L_2G+1S",
        "power_4L_1G+3S",
        "flex_3L_allG",
        "flex_6L_allG",
        "flex_mixed_G+S",
    ):
        rows = buckets.get(name, [])
        print(f"=== {name} ({len(rows)} tickets) ===")
        exact = sum(1 for r in rows if r.get("payout_source") == "exact")
        cal = sum(1 for r in rows if r.get("payout_source") == "calibrated")
        print(f"  payout_source: exact={exact} calibrated={cal}")
        # show up to 8 distinct (deltas, sweep, min, source)
        seen: set[tuple] = set()
        shown = 0
        for r in rows:
            key = (
                tuple(r["goblin_distances"]),
                r.get("power_sweep_x"),
                r.get("power_min_x"),
                r.get("flex_first_x"),
                r.get("payout_source"),
            )
            if key in seen:
                continue
            seen.add(key)
            print(
                f"    deltas={r['goblin_distances']}  "
                f"power {r.get('power_sweep_x')}x / min {r.get('power_min_x')}x  "
                f"flex_first {r.get('flex_first_x')}  "
                f"src={r.get('payout_source')}  ({r.get('group')})"
            )
            shown += 1
            if shown >= 12:
                print(f"    ... ({len(rows) - shown} more tickets, dedupe keys truncated)")
                break
        print()


if __name__ == "__main__":
    main()
