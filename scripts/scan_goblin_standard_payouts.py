#!/usr/bin/env python3
"""
Scan PrizePicks payouts for 2-leg slips: 1 Standard + 1 Goblin (different players).

Uses the same logged-in Chrome + CDP flow as collect_payout_data.py (read-only: never submits).

Prerequisites:
  1) Chrome with remote debugging, logged in on app.prizepicks.com (see docs/chrome_debug_setup.md)
  2) Fresh NBA step1 CSV: NBA/data/outputs/step1_pp_props_today.csv

Pairs are built from step1 only (no step8 pipeline changes). Singles-only: no \"Player A + B\"
rows and no props containing \"combo\". Each ticket uses two different players.

Power 2-leg often shows an all-or-nothing multiplier; Flex 2-leg may expose \"N correct pays\"
(min-style) text — try --ticket-type flex if you need that line in the slip panel.

Example:
  py -3.14 scripts/scan_goblin_standard_payouts.py --cdp-url http://127.0.0.1:9222 --max-pairs 12
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "payout_samples"
DEFAULT_STEP1 = ROOT / "NBA" / "data" / "outputs" / "step1_pp_props_today.csv"

# Reuse CDP + slip helpers (same module as payout collection).
import collect_payout_data as cpd


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _pick_col(df: pd.DataFrame, names: list[str]) -> str | None:
    m = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in m:
            return m[n.lower()]
    return None


def is_combo_player_or_prop(player: Any, prop: Any) -> bool:
    p = str(player or "").strip()
    pr = str(prop or "").strip()
    if " + " in p:
        return True
    if "combo" in pr.lower():
        return True
    return False


def norm_pick_type(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    if "standard" in s or s == "std":
        return "standard"
    return ""


def load_step1_leg_rows(path: Path) -> tuple[list[dict], list[dict]]:
    df = pd.read_csv(path, low_memory=False)
    pcol = _pick_col(df, ["player"])
    prcol = _pick_col(df, ["prop_type", "prop"])
    lcol = _pick_col(df, ["line"])
    ptcol = _pick_col(df, ["pick_type", "pick type"])
    slcol = _pick_col(df, ["standard_line"])
    projcol = _pick_col(df, ["projection_id", "pp_projection_id"])
    if not all([pcol, prcol, lcol, ptcol, projcol]):
        raise RuntimeError(f"step1 missing columns. Have: {list(df.columns)}")

    standards: list[dict] = []
    goblins: list[dict] = []

    for _, r in df.iterrows():
        if is_combo_player_or_prop(r.get(pcol), r.get(prcol)):
            continue
        line = pd.to_numeric(r.get(lcol), errors="coerce")
        if pd.isna(line) or float(line) <= 0.5:
            continue
        pt = norm_pick_type(r.get(ptcol))
        if pt not in ("standard", "goblin"):
            continue
        player = str(r.get(pcol) or "").strip()
        prop = str(r.get(prcol) or "").strip()
        if not player or not prop:
            continue
        std_ln = pd.to_numeric(r.get(slcol), errors="coerce") if slcol else None
        dist = None
        if pt == "goblin" and std_ln is not None and not pd.isna(std_ln):
            dist = abs(float(line) - float(std_ln))
        leg = {
            "player": player,
            "prop_type": prop,
            "line": float(line),
            "pick_type": pt,
            "direction": "OVER",
            "projection_id": str(r.get(projcol) or "").strip(),
            "standard_line": float(std_ln) if std_ln is not None and not pd.isna(std_ln) else None,
            "line_distance": float(dist) if dist is not None else None,
        }
        if pt == "standard":
            standards.append(leg)
        else:
            goblins.append(leg)

    # Dedupe by player+prop+line+pick
    def dedupe(legs: list[dict]) -> list[dict]:
        seen: set[tuple[Any, ...]] = set()
        out: list[dict] = []
        for L in legs:
            k = (_norm(L["player"]), _norm(L["prop_type"]), round(L["line"], 3), L["pick_type"])
            if k in seen:
                continue
            seen.add(k)
            out.append(L)
        return out

    standards = dedupe(standards)
    goblins = dedupe(goblins)
    # Goblin: prefer known distance, sort by distance descending (spread across ticket set)
    goblins.sort(
        key=lambda g: (g.get("line_distance") is None, -(g.get("line_distance") or -1.0)),
    )
    standards.sort(key=lambda s: _norm(s["player"]))
    return standards, goblins


def pick_std_for_goblin(standards: list[dict], gob: dict) -> dict | None:
    gn = _norm(gob["player"])
    for s in standards:
        if _norm(s["player"]) != gn:
            return s
    return None


def build_pairs(
    standards: list[dict],
    goblins: list[dict],
    max_pairs: int,
    balance_buckets: bool,
) -> list[tuple[dict, dict]]:
    if not standards or not goblins:
        return []

    def bucket(d: float | None) -> str:
        if d is None:
            return "unk"
        if d <= 3.0:
            return "small"
        if d <= 6.0:
            return "med"
        return "large"

    if not balance_buckets:
        out: list[tuple[dict, dict]] = []
        for g in goblins:
            if len(out) >= max_pairs:
                break
            s = pick_std_for_goblin(standards, g)
            if s:
                out.append((s, g))
        return out

    by_b: dict[str, list[dict]] = {"small": [], "med": [], "large": [], "unk": []}
    for g in goblins:
        by_b[bucket(g.get("line_distance"))].append(g)

    out = []
    # Round-robin buckets so we sample each distance regime
    order = ["large", "med", "small", "unk"]
    bi = 0
    while len(out) < max_pairs:
        progressed = False
        for _ in range(len(order)):
            b = order[bi % len(order)]
            bi += 1
            pool = by_b[b]
            if not pool:
                continue
            g = pool.pop(0)
            s = pick_std_for_goblin(standards, g)
            if not s:
                continue
            out.append((s, g))
            progressed = True
            if len(out) >= max_pairs:
                break
        if not progressed:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="CDP scan: Standard+Goblin 2-leg payouts from step1")
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    ap.add_argument("--step1", type=Path, default=DEFAULT_STEP1)
    ap.add_argument("--max-pairs", type=int, default=15)
    ap.add_argument(
        "--ticket-type",
        choices=["power", "flex"],
        default="power",
        help="Slip mode in the UI (Flex more likely to show N-correct min text).",
    )
    ap.add_argument("--delay-ms", type=int, default=900, help="Pause after adding second leg")
    ap.add_argument("--no-bucket-balance", action="store_true", help="Walk goblins in distance order only")
    args = ap.parse_args()

    if not args.step1.is_file():
        raise SystemExit(f"Missing step1 file: {args.step1}")

    standards, goblins = load_step1_leg_rows(args.step1)
    pairs = build_pairs(
        standards,
        goblins,
        max_pairs=max(1, int(args.max_pairs)),
        balance_buckets=not args.no_bucket_balance,
    )
    if not pairs:
        raise SystemExit("No standard+goblin pairs after filters (need singles with two player types).")

    print(f"[PLAN] {len(pairs)} pairs (standard + goblin, different players). Ticket={args.ticket_type.upper()}")
    for i, (s, g) in enumerate(pairs, 1):
        d = g.get("line_distance")
        ds = f"{d:.2f}" if d is not None else "n/a"
        print(f"  {i}. STD {s['player']} {s['line']} {s['prop_type']}  |  GOB {g['player']} {g['line']} {g['prop_type']} (dist={ds})")

    p, browser, context, page = cpd.connect_existing_browser(args.cdp_url)
    out_rows: list[dict[str, Any]] = []
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        frame = cpd.find_prizepicks_frame(page)
        cpd.ensure_popular_filter(frame, page)
        cpd.dismiss_modal(frame, page)

        for i, (std_leg, gob_leg) in enumerate(pairs, 1):
            print(f"\n[PAIR {i}/{len(pairs)}] Building slip...")
            frame = cpd.soft_reset(frame, page)
            cpd.dismiss_modal(frame, page)
            cpd.set_ticket_type(frame, args.ticket_type)
            cpd.clear_slip(frame)
            _, frame = cpd.verify_slip_empty(frame, page)
            cpd.dismiss_modal(frame, page)

            ok1 = cpd.add_leg(frame, page, std_leg)
            page.wait_for_timeout(350)
            ok2 = cpd.add_leg(frame, page, gob_leg)
            if not ok1 or not ok2:
                print("  [SKIP] Could not add both legs (board/search mismatch).")
                cpd.clear_slip(frame)
                continue

            page.wait_for_timeout(int(args.delay_ms))
            slip = cpd.read_slip(frame, n_legs=2, ticket_type=args.ticket_type)

            legs_json = [
                {
                    "player": std_leg["player"],
                    "prop_type": std_leg["prop_type"],
                    "line": std_leg["line"],
                    "pick_type": "standard",
                    "direction": std_leg["direction"].lower(),
                    "projection_id": std_leg.get("projection_id", ""),
                },
                {
                    "player": gob_leg["player"],
                    "prop_type": gob_leg["prop_type"],
                    "line": gob_leg["line"],
                    "pick_type": "goblin",
                    "direction": gob_leg["direction"].lower(),
                    "projection_id": gob_leg.get("projection_id", ""),
                    "standard_line": gob_leg.get("standard_line"),
                    "line_distance": gob_leg.get("line_distance"),
                },
            ]

            rec = {
                "timestamp": ts,
                "scan": "goblin_standard_2leg",
                "ticket_type": args.ticket_type,
                "n_legs": 2,
                "n_goblins": 1,
                "n_standard": 1,
                "goblin_line_distance": gob_leg.get("line_distance"),
                "standard_player": std_leg["player"],
                "goblin_player": gob_leg["player"],
                "legs": json.dumps(legs_json, ensure_ascii=False),
                "displayed_multiplier": slip.get("displayed_multiplier"),
                "first_place_payout": slip.get("first_place_payout"),
                "min_guarantee_payout": slip.get("min_guarantee_payout"),
                "min_guarantee_hits_required": slip.get("min_guarantee_hits_required"),
                "to_win_amount": slip.get("to_win"),
                "entry_amount": slip.get("entry_amount"),
                "computed_multiplier": slip.get("computed_multiplier"),
                "raw_slip_section": (slip.get("raw_slip_section") or "")[:500],
            }
            out_rows.append(rec)
            print(
                f"  [READ] first={rec['first_place_payout']} min_g={rec['min_guarantee_payout']} "
                f"displayed={rec['displayed_multiplier']}"
            )

            cpd.clear_slip(frame)
            _, frame = cpd.verify_slip_empty(frame, page)
            cpd.dismiss_modal(frame, page)
            page.wait_for_timeout(500)

    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.utcnow().strftime("%Y-%m-%d")
    out_csv = SAMPLES_DIR / f"goblin_standard_scan_{date_tag}.csv"
    if out_rows:
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
    print(f"\n[DONE] Wrote {len(out_rows)} rows -> {out_csv}")


if __name__ == "__main__":
    main()
