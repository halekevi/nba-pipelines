#!/usr/bin/env python3
"""
Compare production win-rate main vs opt3 shadow (Goblin Tier A only) on live decided tickets.

Shadow exports are written by combined_slate_tickets.py as:
  ui_runner/data/combined_slate_tickets_winrate_goblin_opt3_<date>.json

Baseline is the production main track:
  ui_runner/data/combined_slate_tickets_<date>.json

Validation bar: n>=30 decided shadow tickets on **live** exports only, and clean
shadow ticket HR must beat clean baseline on the same window (ready_to_ship).
Backfill/rebuild exports never count toward the bar (generated_at vs slate date).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.combined_export_trust import day_export_trust  # noqa: E402
from scripts.ticket_slip_grader import (  # noqa: E402
    grade_slip as _grade_slip,
    iter_payload_slips as _iter_slips,
    summarize_ticket_grades as _summarize_ticket_grades,
)
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


def _ticket_hr(slips: list[dict], date_str: str, result_map: dict[str, str]) -> dict[str, Any]:
    graded = [_grade_slip(s, date_str, result_map) for s in slips]
    stats = _summarize_ticket_grades(graded)
    return stats


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


def _sum_live(days: list[dict], prefix: str) -> tuple[int, int]:
    """Sum decided/paid on live export days that have shadow tickets (paired window)."""
    decided = paid = 0
    for d in days:
        if str(d.get("export_trust") or "live") != "live":
            continue
        if int(d.get("shadow_decidable") or 0) == 0:
            continue
        decided += int(d.get(f"{prefix}_decidable") or 0)
        paid += int(d.get(f"{prefix}_paid") or 0)
    return decided, paid


def _ready_to_ship_reason(clean_sd: int, clean_shr: float | None, clean_bhr: float | None) -> str:
    if clean_sd < _MIN_DECIDED:
        need = _MIN_DECIDED - clean_sd
        return f"need {need} more live shadow decided tickets (have {clean_sd}/{_MIN_DECIDED})"
    if clean_shr is None or clean_bhr is None:
        return "missing clean shadow or baseline ticket HR"
    if clean_shr <= clean_bhr:
        return (
            f"clean shadow ticket HR {100*clean_shr:.1f}% "
            f"does not beat clean baseline {100*clean_bhr:.1f}%"
        )
    return "clean sample bar met and shadow beats baseline"


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

    all_sd = sum(int(d.get("shadow_decidable") or 0) for d in days)
    all_sp = sum(int(d.get("shadow_paid") or 0) for d in days)
    all_bd = sum(int(d.get("baseline_decidable") or 0) for d in days)
    all_bp = sum(int(d.get("baseline_paid") or 0) for d in days)

    clean_sd, clean_sp = _sum_live(days, "shadow")
    clean_bd, clean_bp = _sum_live(days, "baseline")
    clean_shr = (clean_sp / clean_sd) if clean_sd else None
    clean_bhr = (clean_bp / clean_bd) if clean_bd else None

    ready = (
        clean_sd >= _MIN_DECIDED
        and clean_shr is not None
        and clean_bhr is not None
        and clean_shr > clean_bhr
    )
    reason = _ready_to_ship_reason(clean_sd, clean_shr, clean_bhr)

    out = {
        "policy": "opt3_goblin_tier_a_shadow",
        "min_decided_bar": _MIN_DECIDED,
        "updated_at": date.today().isoformat(),
        "shadow_decided_total": clean_sd,
        "shadow_paid_total": clean_sp,
        "shadow_ticket_hit_rate": clean_shr,
        "baseline_decided_total": clean_bd,
        "baseline_paid_total": clean_bp,
        "baseline_ticket_hit_rate": clean_bhr,
        "all_days_shadow_decided_total": all_sd,
        "all_days_shadow_paid_total": all_sp,
        "all_days_shadow_ticket_hit_rate": (all_sp / all_sd) if all_sd else None,
        "all_days_baseline_decided_total": all_bd,
        "all_days_baseline_paid_total": all_bp,
        "all_days_baseline_ticket_hit_rate": (all_bp / all_bd) if all_bd else None,
        "ready_to_ship": ready,
        "ready_to_ship_reason": reason,
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
        trust, trust_reason = day_export_trust(
            date_str=date_str,
            baseline_path=base_path,
            shadow_path=shadow_path,
            baseline_payload=base_payload,
            shadow_payload=shadow_payload,
        )
        bhr = base_stats["ticket_hit_rate"]
        shr = shadow_stats["ticket_hit_rate"]
        delta = (shr - bhr) if shr is not None and bhr is not None else None
        row = {
            "date": date_str,
            "export_trust": trust,
            "export_trust_reason": trust_reason,
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
        tag = "" if trust == "live" else " [backfill]"
        print(
            f"{date_str:<12} {b_s:>8} {s_s:>10} {d_s:>8} "
            f"{base_stats['decidable']:7d} {shadow_stats['decidable']:7d}{tag}"
        )

    if not day_rows:
        print("No graded days with baseline and/or shadow ticket exports found.")
        return 1

    track = _update_track_record(day_rows)
    print("\n=== Cumulative LIVE exports only (decided tickets) ===")
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
    all_sd = int(track.get("all_days_shadow_decided_total") or 0)
    if all_sd and all_sd != sd:
        asp = int(track.get("all_days_shadow_paid_total") or 0)
        print(
            f"\n  (All days incl. backfill: shadow {asp}/{all_sd} = "
            f"{100*asp/all_sd:.1f}% — excluded from ship bar)"
        )
    print(f"\n  Live shadow decided: {sd}/{_MIN_DECIDED} toward sample bar")
    if track["ready_to_ship"]:
        print("  STATUS: ready_to_ship=true — live n>=30 and shadow beats baseline.")
    else:
        print(f"  STATUS: ready_to_ship=false — {track.get('ready_to_ship_reason')}")
    print(f"  Track file -> {_TRACK_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
