#!/usr/bin/env python3
"""
Compare production win-rate main vs opt3 shadow (Goblin Tier A only) on live decided tickets.

Shadow exports are written by combined_slate_tickets.py as:
  ui_runner/data/combined_slate_tickets_winrate_goblin_opt3_<date>.json

Baseline is the production main track:
  ui_runner/data/combined_slate_tickets_<date>.json

Validation bar (same as STRONG): n>=30 decided shadow tickets before shipping opt3.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_TRACK_PATH = _REPO / "data" / "reports" / "winrate_goblin_opt3_shadow_track.json"
_MIN_DECIDED = 30


def _load_graded_props(date_str: str) -> pd.DataFrame:
    for rel in (
        _REPO / "mobile" / "www" / f"graded_props_{date_str}.json",
        _REPO / "ui_runner" / "templates" / f"graded_props_{date_str}.json",
        _REPO / "ui_runner" / "data" / f"graded_props_{date_str}.json",
    ):
        if not rel.is_file():
            continue
        try:
            data = json.loads(rel.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = data if isinstance(data, list) else data.get("props", data.get("rows", []))
        if not isinstance(rows, list):
            continue
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df["grade_date"] = date_str
        return df
    return pd.DataFrame()


def _props_index(props: pd.DataFrame) -> dict[str, str]:
    props = props.copy()
    prop_col = "prop_type" if "prop_type" in props.columns else "prop"
    props["join_key"] = (
        props["grade_date"].astype(str)
        + "|"
        + props["player"].astype(str).str.casefold()
        + "|"
        + props[prop_col].astype(str).str.casefold()
        + "|"
        + pd.to_numeric(props["line"], errors="coerce").round(2).astype(str)
    )
    hit = props.get("hit")
    if hit is not None:
        props["result"] = np.where(
            pd.to_numeric(hit, errors="coerce").eq(1),
            "HIT",
            np.where(pd.to_numeric(hit, errors="coerce").eq(0), "MISS", props.get("result")),
        )
    return props.drop_duplicates("join_key").set_index("join_key")["result"].astype(str).str.upper().to_dict()


def _iter_slips(payload: dict) -> list[dict]:
    out: list[dict] = []
    for g in payload.get("groups") or []:
        for slip in g.get("tickets") or []:
            if isinstance(slip, dict):
                out.append(slip)
    return out


def _grade_slip(slip: dict, date_str: str, result_map: dict[str, str]) -> dict[str, Any]:
    legs = list(slip.get("legs") or slip.get("rows") or [])
    grades: list[str] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        key = (
            f"{date_str}|"
            f"{str(leg.get('player', '')).casefold()}|"
            f"{str(leg.get('prop_type') or leg.get('prop') or '').casefold()}|"
            f"{round(float(leg.get('line') or 0), 2)}"
        )
        grades.append(str(result_map.get(key, "PENDING")).upper())
    decided = [g for g in grades if g in ("HIT", "MISS")]
    voids = sum(1 for g in grades if g == "VOID")
    paid = bool(decided) and all(g == "HIT" for g in decided) and len(decided) >= 2
    return {
        "ticket_id": slip.get("ticket_id"),
        "n_legs": len(legs),
        "n_decided": len(decided),
        "n_void": voids,
        "paid": paid,
        "all_decided": len(decided) == len(legs) and len(legs) > 0,
    }


def _ticket_hr(slips: list[dict], date_str: str, result_map: dict[str, str]) -> dict[str, Any]:
    graded = [_grade_slip(s, date_str, result_map) for s in slips]
    decidable = [g for g in graded if g["all_decided"]]
    paid = sum(1 for g in decidable if g["paid"])
    return {
        "slips": len(slips),
        "decidable": len(decidable),
        "paid": paid,
        "ticket_hit_rate": (paid / len(decidable)) if decidable else None,
    }


def _load_payload(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _dated_paths(date_str: str) -> tuple[Path, Path]:
    data = _REPO / "ui_runner" / "data"
    baseline = data / f"combined_slate_tickets_{date_str}.json"
    shadow = data / f"combined_slate_tickets_winrate_goblin_opt3_{date_str}.json"
    return baseline, shadow


def _update_track_record(day_rows: list[dict]) -> dict:
    existing: dict = {}
    if _TRACK_PATH.is_file():
        try:
            existing = json.loads(_TRACK_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    by_date = {str(r.get("date")): r for r in existing.get("days", []) if r.get("date")}
    for row in day_rows:
        by_date[str(row["date"])] = row
    days = sorted(by_date.values(), key=lambda r: str(r.get("date") or ""))
    shadow_decided = sum(int(d.get("shadow_decidable") or 0) for d in days)
    shadow_paid = sum(int(d.get("shadow_paid") or 0) for d in days)
    baseline_decided = sum(int(d.get("baseline_decidable") or 0) for d in days)
    baseline_paid = sum(int(d.get("baseline_paid") or 0) for d in days)
    out = {
        "policy": "opt3_goblin_tier_a_shadow",
        "min_decided_bar": _MIN_DECIDED,
        "updated_at": date.today().isoformat(),
        "shadow_decided_total": shadow_decided,
        "shadow_paid_total": shadow_paid,
        "shadow_ticket_hit_rate": (shadow_paid / shadow_decided) if shadow_decided else None,
        "baseline_decided_total": baseline_decided,
        "baseline_paid_total": baseline_paid,
        "baseline_ticket_hit_rate": (baseline_paid / baseline_decided) if baseline_decided else None,
        "ready_to_ship": shadow_decided >= _MIN_DECIDED,
        "days": days,
    }
    _TRACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TRACK_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="date_from", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=21, help="Trailing days when --from/--to omitted")
    args = ap.parse_args()

    if args.date_from and args.date_to:
        start = date.fromisoformat(args.date_from)
        end = date.fromisoformat(args.date_to)
    else:
        end = date.today()
        start = end - timedelta(days=max(1, int(args.days)))
    dates = []
    d = start
    while d <= end:
        dates.append(d.isoformat())
        d += timedelta(days=1)

    day_rows: list[dict] = []
    print(f"Win-rate Goblin opt3 shadow vs baseline ({dates[0]} → {dates[-1]})")
    print(f"{'Date':<12} {'Base HR':>8} {'Shadow HR':>10} {'Δ':>8} {'base_n':>7} {'shad_n':>7}")
    for date_str in dates:
        props = _load_graded_props(date_str)
        if props.empty:
            continue
        result_map = _props_index(props)
        base_path, shadow_path = _dated_paths(date_str)
        base_payload = _load_payload(base_path)
        shadow_payload = _load_payload(shadow_path)
        if not base_payload and not shadow_payload:
            continue
        base_stats = _ticket_hr(_iter_slips(base_payload or {}), date_str, result_map)
        shadow_stats = _ticket_hr(_iter_slips(shadow_payload or {}), date_str, result_map)
        if base_stats["decidable"] == 0 and shadow_stats["decidable"] == 0:
            continue
        bhr = base_stats["ticket_hit_rate"]
        shr = shadow_stats["ticket_hit_rate"]
        delta = (shr - bhr) if shr is not None and bhr is not None else None
        row = {
            "date": date_str,
            "baseline_slips": base_stats["slips"],
            "baseline_decidable": base_stats["decidable"],
            "baseline_paid": base_stats["paid"],
            "baseline_ticket_hit_rate": round(bhr, 4) if bhr is not None else None,
            "shadow_slips": shadow_stats["slips"],
            "shadow_decidable": shadow_stats["decidable"],
            "shadow_paid": shadow_stats["paid"],
            "shadow_ticket_hit_rate": round(shr, 4) if shr is not None else None,
            "delta_pp": round(100 * delta, 1) if delta is not None else None,
            "has_shadow_export": shadow_payload is not None,
        }
        day_rows.append(row)
        b_s = f"{100*bhr:.1f}%" if bhr is not None else "—"
        s_s = f"{100*shr:.1f}%" if shr is not None else "—"
        d_s = f"{100*delta:+.1f}pp" if delta is not None else "—"
        print(
            f"{date_str:<12} {b_s:>8} {s_s:>10} {d_s:>8} "
            f"{base_stats['decidable']:7d} {shadow_stats['decidable']:7d}"
        )

    if not day_rows:
        print("No graded days with baseline and/or shadow ticket exports found.")
        return 1

    track = _update_track_record(day_rows)
    print("\n=== Cumulative (decided tickets only) ===")
    sd = int(track["shadow_decided_total"])
    sp = int(track["shadow_paid_total"])
    bd = int(track["baseline_decided_total"])
    bp = int(track["baseline_paid_total"])
    if bd:
        print(f"  Baseline: {bp}/{bd} = {100*bp/bd:.1f}% ticket HR")
    if sd:
        print(f"  Shadow:   {sp}/{sd} = {100*sp/sd:.1f}% ticket HR")
        if bd:
            print(f"  Delta:    {100*(sp/sd - bp/bd):+.1f}pp")
    print(f"\n  Shadow decided tickets: {sd}/{_MIN_DECIDED} toward validation bar")
    if track["ready_to_ship"]:
        print("  STATUS: n>=30 shadow tickets decided — eligible for ship review (not auto-ship).")
    else:
        print(f"  STATUS: need {_MIN_DECIDED - sd} more decided shadow tickets before ship review.")
    print(f"  Track file -> {_TRACK_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
