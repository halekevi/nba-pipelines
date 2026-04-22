#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from build_ticket_eval import (
    _dated_candidates,
    _filter_payload_groups,
    _leg_grade,
    _load_actuals_indices,
    _load_tickets,
    _match_leg_to_row_multi,
    _ticket_eval_money_outcome,
    _ticket_is_flex_play_structure,
    resolve_ticket_eval_graded_merge_dates,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "ml" / "ticket_training_dataset.csv"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if np.isnan(v) or np.isinf(v):
        return None
    return v


def _agg(vals: list[float | None], fn: str) -> float | None:
    arr = [float(v) for v in vals if v is not None]
    if not arr:
        return None
    if fn == "mean":
        return float(np.mean(arr))
    if fn == "min":
        return float(np.min(arr))
    if fn == "max":
        return float(np.max(arr))
    if fn == "std":
        return float(np.std(arr))
    raise ValueError(f"Unknown agg fn: {fn}")


def _sports_counter(legs: list[dict[str, Any]]) -> Counter[str]:
    c: Counter[str] = Counter()
    for leg in legs:
        s = str(leg.get("sport") or "").strip().upper()
        if s:
            c[s] += 1
    return c


def _picktype_counter(legs: list[dict[str, Any]]) -> Counter[str]:
    c: Counter[str] = Counter()
    for leg in legs:
        pt = str(leg.get("pick_type") or "").strip().upper()
        if "DEMON" in pt:
            c["DEMON"] += 1
        elif "GOBLIN" in pt:
            c["GOBLIN"] += 1
        else:
            c["STANDARD"] += 1
    return c


def _infer_group_type(group_name: str) -> str:
    g = str(group_name or "").strip().lower()
    if "flex" in g:
        return "FLEX"
    if "power" in g or "pwr" in g:
        return "POWER"
    return "OTHER"


def _discover_ticket_jsons() -> dict[str, Path]:
    """
    Date -> combined_slate_tickets path.
    Prefer outputs/YYYY-MM-DD copy over root copy when both exist.
    """
    out: dict[str, Path] = {}

    # Root-level archives
    for p in ROOT.glob("combined_slate_tickets_*.json"):
        m = re.match(r"^combined_slate_tickets_(\d{4}-\d{2}-\d{2})", p.name)
        if not m:
            continue
        out[m.group(1)] = p

    # outputs/YYYY-MM-DD/ archives (preferred)
    outputs_dir = ROOT / "outputs"
    if outputs_dir.is_dir():
        for p in outputs_dir.glob("*/combined_slate_tickets_*.json"):
            m = re.match(r"^combined_slate_tickets_(\d{4}-\d{2}-\d{2})", p.name)
            if not m:
                continue
            out[m.group(1)] = p

    return out


def _ticket_row(
    slate_date: str,
    group_name: str,
    ticket_idx: int,
    ticket: dict[str, Any],
    indices: dict[str, tuple[dict, dict]],
) -> dict[str, Any]:
    legs = list(ticket.get("legs") or [])
    leg_grades: list[str] = []
    leg_sports: list[str] = []

    ml_probs: list[float | None] = []
    leg_probs_used: list[float | None] = []
    edges: list[float | None] = []
    abs_edges: list[float | None] = []
    hit_rates: list[float | None] = []
    rank_scores: list[float | None] = []
    context_scores: list[float | None] = []
    intel_hit_rates: list[float | None] = []

    for leg in legs:
        row = _match_leg_to_row_multi(leg, indices)

        line_f = _safe_float(leg.get("line"))
        if row and row.get("line") is not None and line_f is None:
            line_f = _safe_float(row.get("line"))

        direction = str(leg.get("direction") or "").strip().upper()
        actual = row["actual"] if row else None
        graw = row["grade_raw"] if row else ""
        vnote = row.get("void_note", "") if row else ""
        grade = _leg_grade(actual, line_f, direction, graw, vnote)
        leg_grades.append(grade)

        leg_sports.append(str(leg.get("sport") or "").strip().upper())
        ml_probs.append(_safe_float(leg.get("ml_prob")))
        leg_probs_used.append(_safe_float(leg.get("leg_prob_used")))
        edges.append(_safe_float(leg.get("edge")))
        abs_edges.append(_safe_float(leg.get("abs_edge")))
        hit_rates.append(_safe_float(leg.get("hit_rate")))
        rank_scores.append(_safe_float(leg.get("rank_score")))
        context_scores.append(_safe_float(leg.get("context_score")))
        intel_hit_rates.append(_safe_float(leg.get("intel_season_hit_rate")))

    outcome = _ticket_eval_money_outcome(group_name, leg_grades, ticket)
    pending = bool(outcome.get("pending"))
    result = str(outcome.get("result") or "")

    n_legs = len(legs)
    n_hit = sum(1 for g in leg_grades if g == "HIT")
    n_miss = sum(1 for g in leg_grades if g == "MISS")
    n_void = sum(1 for g in leg_grades if g == "VOID")
    n_ungraded = sum(1 for g in leg_grades if g == "UNGRADED")

    sport_counts = _sports_counter(legs)
    pick_counts = _picktype_counter(legs)
    dominant_sport = sport_counts.most_common(1)[0][0] if sport_counts else ""

    label_cash: int | None = None
    if not pending:
        if result in ("WIN", "SWEEP", "MIN GUARANTEE"):
            label_cash = 1
        elif result in ("LOSS", "VOID_LOSS"):
            label_cash = 0

    actual_payout = _safe_float(outcome.get("actual_payout"))
    net_10 = _safe_float(outcome.get("net_10"))

    row_out: dict[str, Any] = {
        "slate_date": slate_date,
        "group_name": group_name,
        "group_type": _infer_group_type(group_name),
        "ticket_no": ticket.get("ticket_no", ticket_idx),
        "ticket_uid": f"{slate_date}|{group_name}|{ticket.get('ticket_no', ticket_idx)}|{ticket_idx}",
        "n_legs": n_legs,
        "is_flex_structure": int(_ticket_is_flex_play_structure(group_name, n_legs)),
        "sports_in_ticket": len(sport_counts),
        "dominant_sport": dominant_sport,
        "legs_nba": sport_counts.get("NBA", 0) + sport_counts.get("NBA1H", 0) + sport_counts.get("NBA1Q", 0),
        "legs_cbb": sport_counts.get("CBB", 0) + sport_counts.get("WCBB", 0),
        "legs_nhl": sport_counts.get("NHL", 0),
        "legs_soccer": sport_counts.get("SOCCER", 0),
        "legs_mlb": sport_counts.get("MLB", 0),
        "pick_standard_count": pick_counts.get("STANDARD", 0),
        "pick_goblin_count": pick_counts.get("GOBLIN", 0),
        "pick_demon_count": pick_counts.get("DEMON", 0),
        "n_hit": n_hit,
        "n_miss": n_miss,
        "n_void": n_void,
        "n_ungraded": n_ungraded,
        "pending": int(pending),
        "result": result,
        "label_cash": label_cash,
        "label_paid": int(actual_payout is not None and actual_payout > 0.0) if not pending else None,
        "actual_payout_mult": actual_payout,
        "net_10": net_10,
        "predicted_payout_mult": _safe_float(outcome.get("predicted_payout")),
        "predicted_p_win": _safe_float(outcome.get("predicted_p_win")),
        "predicted_ev": _safe_float(outcome.get("predicted_ev")),
        "ticket_objective_score": _safe_float(ticket.get("ticket_objective_score")),
        "ev_power": _safe_float(ticket.get("ev_power")),
        "est_ev": _safe_float(ticket.get("est_ev")),
        "flat_ev": _safe_float(ticket.get("flat_ev")),
        "payout_multiplier": _safe_float(ticket.get("payout_multiplier")),
        "power_payout": _safe_float(ticket.get("power_payout")),
        "flex_payout": _safe_float(ticket.get("flex_payout")),
        "est_win_prob": _safe_float(ticket.get("est_win_prob")),
        "avg_hit_rate_leg": _agg(hit_rates, "mean"),
        "avg_ml_prob_leg": _agg(ml_probs, "mean"),
        "min_ml_prob_leg": _agg(ml_probs, "min"),
        "max_ml_prob_leg": _agg(ml_probs, "max"),
        "std_ml_prob_leg": _agg(ml_probs, "std"),
        "avg_leg_prob_used": _agg(leg_probs_used, "mean"),
        "min_leg_prob_used": _agg(leg_probs_used, "min"),
        "avg_edge_leg": _agg(edges, "mean"),
        "min_edge_leg": _agg(edges, "min"),
        "max_edge_leg": _agg(edges, "max"),
        "avg_abs_edge_leg": _agg(abs_edges, "mean"),
        "avg_rank_score_leg": _agg(rank_scores, "mean"),
        "min_rank_score_leg": _agg(rank_scores, "min"),
        "avg_context_score_leg": _agg(context_scores, "mean"),
        "avg_intel_hit_rate_leg": _agg(intel_hit_rates, "mean"),
    }
    return row_out


def _build_rows_for_payload(
    slate_date: str,
    payload: dict[str, Any],
    include_undecided: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    payload = _filter_payload_groups(payload, debug=False)
    merge_dates, _ = resolve_ticket_eval_graded_merge_dates(slate_date, payload, extra_iso_dates=None)
    indices = _load_actuals_indices(_dated_candidates(slate_date), merge_dates)

    rows: list[dict[str, Any]] = []
    stats = {"tickets_total": 0, "tickets_kept": 0, "tickets_pending": 0, "tickets_decided": 0}

    for group in payload.get("groups") or []:
        gname = str(group.get("group_name") or "Group")
        tickets = list(group.get("tickets") or [])
        for i, ticket in enumerate(tickets, start=1):
            stats["tickets_total"] += 1
            row = _ticket_row(slate_date, gname, i, ticket, indices)
            if row["pending"]:
                stats["tickets_pending"] += 1
                if not include_undecided:
                    continue
            else:
                stats["tickets_decided"] += 1
            stats["tickets_kept"] += 1
            rows.append(row)
    return rows, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill ticket-level ML training dataset from historical combined ticket JSON + grading.")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="CSV output path.")
    ap.add_argument("--start-date", default="", help="Optional YYYY-MM-DD lower bound.")
    ap.add_argument("--end-date", default="", help="Optional YYYY-MM-DD upper bound.")
    ap.add_argument("--include-undecided", action="store_true", help="Include pending/ungraded tickets (label columns stay null).")
    ap.add_argument("--limit-files", type=int, default=0, help="Optional max number of dates to process (newest first).")
    args = ap.parse_args()

    by_date = _discover_ticket_jsons()
    if not by_date:
        raise FileNotFoundError("No combined_slate_tickets_*.json files found for backfill.")

    dates = sorted(d for d in by_date.keys() if DATE_RE.match(d))
    if args.start_date:
        dates = [d for d in dates if d >= args.start_date]
    if args.end_date:
        dates = [d for d in dates if d <= args.end_date]
    if args.limit_files and args.limit_files > 0:
        dates = sorted(dates, reverse=True)[: int(args.limit_files)]
        dates = sorted(dates)

    all_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    print(f"[ticket-dataset] Processing {len(dates)} date(s)...")
    for d in dates:
        p = by_date[d]
        try:
            payload = _load_tickets(p, d)
        except Exception as e:
            print(f"  [skip] {d} load failed from {p}: {type(e).__name__}: {e}")
            continue

        rows, stats = _build_rows_for_payload(d, payload, include_undecided=bool(args.include_undecided))
        all_rows.extend(rows)
        summary_rows.append({"date": d, **stats, "source": str(p)})
        print(
            f"  {d}: total={stats['tickets_total']} kept={stats['tickets_kept']} "
            f"decided={stats['tickets_decided']} pending={stats['tickets_pending']}"
        )

    if not all_rows:
        raise RuntimeError("No ticket rows produced. Check source JSON availability and grading artifacts.")

    df = pd.DataFrame(all_rows)
    if "slate_date" in df.columns:
        df = df.sort_values(["slate_date", "group_name", "ticket_no"], ascending=[True, True, True], na_position="last")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    # Write sidecar summary for quick diagnostics.
    sm = pd.DataFrame(summary_rows)
    summary_path = out_path.with_name(out_path.stem + "_summary.csv")
    if not sm.empty:
        sm.to_csv(summary_path, index=False, encoding="utf-8-sig")

    decided = int(df["label_cash"].isin([0, 1]).sum()) if "label_cash" in df.columns else 0
    paid = int((df["label_paid"] == 1).sum()) if "label_paid" in df.columns else 0
    print(f"\n[ticket-dataset] Wrote -> {out_path}")
    print(f"rows={len(df)} decided={decided} paid={paid}")
    if not sm.empty:
        print(f"summary -> {summary_path}")


if __name__ == "__main__":
    main()
