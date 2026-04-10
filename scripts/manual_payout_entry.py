#!/usr/bin/env python3
"""
Manual payout entry for goblin/demon *deviation* from PrizePicks baselines.

Published baselines (standard legs — no need to fit those):
  Legs  Power  Flex 1st  Flex miss-1
  2     3x     3x       -
  3     6x     3x       1.25x
  4     10x    5x       1.5x
  5     20x    10x      2x
  6     37.5x  25x      2x

This script walks six fixed Power slips (goblin signal + standards where needed),
saves rows for scripts/fit_payout_formula.py, then runs the fitter.
No step8 / Excel — legs are fixed from the calibration slate.
"""

from __future__ import annotations

import csv
import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "payout_samples"

# Fixed legs: goblin line_distance from the calibration slate; direction placeholder for JSON.
_STD = "standard"
_GOB = "goblin"
_DIR = "over"


def _leg(
    player: str,
    prop: str,
    line: float,
    pick: str,
    dist: float | None,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "player": player,
        "prop_type": prop,
        "line": line,
        "pick_type": pick,
        "direction": _DIR,
    }
    if dist is not None:
        d["line_distance"] = float(dist)
    return d


# Six tickets — matches the suggested goblin-signal slate (A–F).
FIXED_SIX_TICKETS: list[dict[str, Any]] = [
    {
        "ticket_id": "A",
        "ticket_num": 1,
        "n_legs": 2,
        "ticket_type": "power",
        "title": "2-leg Power, 1 small goblin (dist ~1) + 1 standard",
        "legs": [
            _leg("Kris Dunn", "Assists", 2.5, _GOB, 1.0),
            _leg("Ajay Mitchell + Ace Bailey", "Points (Combo)", 31.0, _STD, None),
        ],
    },
    {
        "ticket_id": "B",
        "ticket_num": 2,
        "n_legs": 2,
        "ticket_type": "power",
        "title": "2-leg Power, 1 large goblin (dist 6+) + 1 standard",
        "legs": [
            _leg("LeBron James", "Pts+Rebs", 24.5, _GOB, 9.0),
            _leg("Alex Caruso", "Points", 5.0, _STD, None),
        ],
    },
    {
        "ticket_id": "C",
        "ticket_num": 3,
        "n_legs": 2,
        "ticket_type": "power",
        "title": "2-leg Power, 2 goblins (different distances)",
        "legs": [
            _leg("Jayson Tatum", "Pts+Rebs+Asts", 29.5, _GOB, 8.5),
            _leg("Nolan Traore", "Pts+Rebs+Asts", 14.5, _GOB, 7.0),
        ],
    },
    {
        "ticket_id": "D",
        "ticket_num": 4,
        "n_legs": 3,
        "ticket_type": "power",
        "title": "3-leg Power, 1 goblin (dist 6+) + 2 standard",
        "legs": [
            _leg("Jaylen Brown", "Pts+Asts", 24.5, _GOB, 7.5),
            _leg("Anthony Edwards + Brandon Miller", "Points (Combo)", 44.5, _STD, None),
            _leg("Anthony Edwards + Kon Knueppel", "Points (Combo)", 42.5, _STD, None),
        ],
    },
    {
        "ticket_id": "E",
        "ticket_num": 5,
        "n_legs": 3,
        "ticket_type": "power",
        "title": "3-leg Power, 2 goblins + 1 standard",
        "legs": [
            _leg("Kawhi Leonard", "Pts+Asts", 24.5, _GOB, 7.0),
            _leg("Julius Randle", "Pts+Rebs", 19.5, _GOB, 7.0),
            _leg("Anthony Edwards + LaMelo Ball", "Points (Combo)", 45.5, _STD, None),
        ],
    },
    {
        "ticket_id": "F",
        "ticket_num": 6,
        "n_legs": 3,
        "ticket_type": "power",
        "title": "3-leg Power, 3 goblins",
        "legs": [
            _leg("Kon Knueppel", "Pts+Rebs+Asts", 19.5, _GOB, 7.0),
            _leg("Trey Murphy", "Pts+Rebs", 19.5, _GOB, 7.0),
            _leg("Brice Sensabaugh", "Points", 14.5, _GOB, 7.0),
        ],
    },
]


def _count_pick_types(legs: list[dict[str, Any]]) -> tuple[int, int, int]:
    ng = sum(1 for x in legs if str(x.get("pick_type", "")).lower() == "goblin")
    nd = sum(1 for x in legs if str(x.get("pick_type", "")).lower() == "demon")
    ns = sum(1 for x in legs if str(x.get("pick_type", "")).lower() == "standard")
    return ng, nd, ns


def _enrich_tickets() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in FIXED_SIX_TICKETS:
        ng, nd, ns = _count_pick_types(t["legs"])
        row = {**t, "n_goblins": ng, "n_demons": nd, "n_standard": ns}
        out.append(row)
    return out


def leg_label(leg: dict[str, Any]) -> str:
    dist = leg.get("line_distance")
    dist_part = f", dist={float(dist):.2f}" if dist is not None else ", dist=n/a"
    return (
        f"{leg['player']} {leg['line']} {leg['prop_type']} "
        f"({leg['pick_type']}{dist_part})"
    )


def save_suggested_tickets(path: Path, tickets: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for t in tickets:
        lines.append(f"TICKET {t['ticket_id']} — {t['title']}")
        lines.append(f"  Type: {t['ticket_type']} | Legs: {t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            lines.append(f"  Leg {i}: {leg_label(leg)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[SAVE] Suggested tickets -> {path}")


def avg_goblin_distance(legs: list[dict[str, Any]]) -> str:
    ds = [
        float(leg["line_distance"])
        for leg in legs
        if str(leg.get("pick_type", "")).lower() == "goblin"
        and leg.get("line_distance") is not None
    ]
    if not ds:
        return ""
    return f"{sum(ds) / len(ds):.4f}"


def main() -> None:
    print(
        "\nPublished PrizePicks baselines (standard — not collected here):\n"
        "  Legs   Power Play   Flex 1st   Flex miss 1\n"
        "  2      3x           3x         -\n"
        "  3      6x           3x         1.25x\n"
        "  4      10x          5x         1.5x\n"
        "  5      20x          10x        2x\n"
        "  6      37.5x        25x        2x\n"
        "\nBelow: 6 Power slips for goblin deviation only (build each in PrizePicks).\n"
    )

    tickets = _enrich_tickets()
    today = date.today().isoformat()
    suggested_path = SAMPLES_DIR / f"suggested_tickets_{today}.txt"
    save_suggested_tickets(suggested_path, tickets)

    print("=== SIX GOBLIN-SIGNAL TICKETS (A–F) ===\n")
    for t in tickets:
        print(f"TICKET {t['ticket_id']} — {t['title']}")
        print(f"  POWER | n_legs={t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            print(f"  Leg {i}: {leg_label(leg)}")
        print()

    out_csv = SAMPLES_DIR / f"payout_log_manual_{today}.csv"
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "ticket_id",
        "ticket_num",
        "n_legs",
        "ticket_type",
        "n_goblins",
        "n_demons",
        "n_standard",
        "legs_detail",
        "legs",
        "avg_goblin_distance",
        "first_place_payout",
        "min_guarantee_payout",
        "displayed_multiplier",
        "entry_amount",
        "to_win_amount",
        "computed_multiplier",
        "source",
    ]

    saved: list[dict[str, Any]] = []

    print(
        "Enter payouts after each screenshot / slip (6 total).\n"
        "  • Q or quit on first prompt: exit early.\n"
        "  • ENTER on '1st place pays': skip that ticket.\n"
    )

    for t in tickets:
        print("=" * 60)
        print(f"TICKET {t['ticket_id']} — {t['title']}")
        print(f"  Mode: POWER | Legs: {t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            print(f"  Leg {i}: {leg_label(leg)}")
        print("\nBuild this slip in PrizePicks, then enter values:\n")

        fp = input("1st place pays (e.g. 6.0) [Enter=skip ticket, Q=quit]: ").strip()
        if fp.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        if not fp:
            print("  (skipped)\n")
            continue
        try:
            first_place = float(fp)
        except ValueError:
            print("  Invalid number; skipping ticket.\n")
            continue

        mg_raw = input("N correct pays / min guarantee (e.g. 1.25): ").strip()
        if mg_raw.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        min_g = float(mg_raw) if mg_raw else None

        ent_raw = input("Entry amount (e.g. 10.00): ").strip()
        if ent_raw.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        entry = float(ent_raw) if ent_raw else None

        tw_raw = input("To Win amount (e.g. 75.00): ").strip()
        if tw_raw.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        to_win = float(tw_raw) if tw_raw else None

        computed = ""
        if entry and to_win and entry > 0:
            computed = f"{float(to_win) / float(entry):.4f}"

        legs_detail = " | ".join(leg_label(L) for L in t["legs"])
        legs_json = json.dumps(t["legs"], ensure_ascii=False)

        row = {
            "ticket_id": t["ticket_id"],
            "ticket_num": t["ticket_num"],
            "n_legs": t["n_legs"],
            "ticket_type": t["ticket_type"],
            "n_goblins": t["n_goblins"],
            "n_demons": t["n_demons"],
            "n_standard": t["n_standard"],
            "legs_detail": legs_detail,
            "legs": legs_json,
            "avg_goblin_distance": avg_goblin_distance(t["legs"]),
            "first_place_payout": first_place,
            "min_guarantee_payout": min_g if min_g is not None else "",
            "displayed_multiplier": first_place,
            "entry_amount": entry if entry is not None else "",
            "to_win_amount": to_win if to_win is not None else "",
            "computed_multiplier": computed,
            "source": "manual",
        }
        saved.append(row)

        write_header = not out_csv.exists()
        with out_csv.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                w.writeheader()
            w.writerow(row)
        print(f"  [SAVED] -> {out_csv.name}\n")

    if saved:
        print("Running formula fitter on collected data...")
        r = subprocess.run(
            ["py", "-3.14", str(ROOT / "scripts" / "fit_payout_formula.py")],
            cwd=str(ROOT),
        )
        if r.returncode != 0:
            print(f"[WARN] fit_payout_formula.py exited with code {r.returncode}")
    else:
        print("No rows saved; skipping fitter.")


if __name__ == "__main__":
    main()
