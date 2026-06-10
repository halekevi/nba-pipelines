#!/usr/bin/env python3
"""
Backtest: when a ticket leg's player would have appeared on Hot Players (point-in-time),
how often did that leg HIT?

Uses:
  - Graded history before slate date -> same logic as /api/hot-players (tier + slate + top N/sport)
  - combined_slate_tickets_YYYY-MM-DD.json legs graded via build_ticket_eval actuals

Run: py scripts/backtest_hot_players_on_tickets.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Load build_player_consistency_ui
_spec_bpc = importlib.util.spec_from_file_location(
    "build_player_consistency_ui",
    SCRIPTS / "build_player_consistency_ui.py",
)
bpc = importlib.util.module_from_spec(_spec_bpc)
assert _spec_bpc and _spec_bpc.loader
_spec_bpc.loader.exec_module(bpc)

# Load build_ticket_training_dataset helpers
_spec_ttd = importlib.util.spec_from_file_location(
    "build_ticket_training_dataset",
    SCRIPTS / "build_ticket_training_dataset.py",
)
ttd = importlib.util.module_from_spec(_spec_ttd)
assert _spec_ttd and _spec_ttd.loader
_spec_ttd.loader.exec_module(ttd)

_load_tickets = ttd._load_tickets
_filter_payload_groups = ttd._filter_payload_groups
_leg_grade = ttd._leg_grade
_match_leg_to_row_multi = ttd._match_leg_to_row_multi
_normalize_status_reason = ttd._normalize_status_reason
_safe_float = ttd._safe_float
_norm_text = ttd._norm_text
_dated_candidates = ttd._dated_candidates
_load_actuals_indices = ttd._load_actuals_indices
resolve_ticket_eval_graded_merge_dates = ttd.resolve_ticket_eval_graded_merge_dates
_norm_name = bpc._norm_name
_prop_match_key = bpc._prop_match_key

# Period slates (NBA1Q, etc.) share hot-player pools with parent league in the UI.
_HOT_SPORT_PARENT = {
    "NBA1H": "NBA",
    "NBA1Q": "NBA",
    "CBB": "CBB",
    "WCBB": "WNBA",
}


def hot_lookup_sport(sport: str) -> str:
    s = str(sport or "").strip().upper()
    return _HOT_SPORT_PARENT.get(s, s)


def discover_ticket_jsons() -> dict[str, Path]:
    """Prefer ui_runner/data archives (deployed ticket history)."""
    out: dict[str, Path] = {}
    for base in (REPO / "ui_runner" / "data", REPO / "outputs", REPO):
        if not base.is_dir():
            continue
        for p in sorted(base.glob("combined_slate_tickets_*.json")):
            m = re.match(r"combined_slate_tickets_(\d{4}-\d{2}-\d{2})\.json$", p.name)
            if m:
                out.setdefault(m.group(1), p)
    return dict(sorted(out.items()))


def slate_pairs_from_payload(payload: dict[str, Any]) -> tuple[set[str], set[tuple[str, str]]]:
    players: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for group in payload.get("groups") or []:
        for ticket in group.get("tickets") or []:
            for leg in ticket.get("legs") or ticket.get("rows") or []:
                name = str(leg.get("player") or "").strip()
                if not name:
                    continue
                sport = bpc._normalize_slate_sport(str(leg.get("sport") or ""))
                players.add(name)
                pairs.add((_norm_name(name), sport))
    return players, pairs


def hot_player_keys_for_date(
    df: pd.DataFrame,
    slate_date: str,
    slate_players: set[str],
    slate_pairs: set[tuple[str, str]],
    limit: int = 8,
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], dict]]:
    """Return (norm_name, sport) keys that would show on Hot Players for slate_date."""
    pit = df.copy()
    if "file_date" in pit.columns:
        pit["file_date"] = pd.to_datetime(pit["file_date"], errors="coerce")
        cut = pd.to_datetime(slate_date).date()
        pit = pit[pit["file_date"].dt.date < cut]
    elif "step8_game_date" in pit.columns:
        pit["step8_game_date"] = pd.to_datetime(pit["step8_game_date"], errors="coerce")
        cut = pd.to_datetime(slate_date).date()
        pit = pit[pit["step8_game_date"].dt.date < cut]

    if pit.empty:
        return set(), {}

    records = bpc.compute_consistency(pit, min_props=10)
    records = bpc.tag_today_slate(records, slate_players, slate_pairs)

    today = [p for p in records if p.get("on_today_slate") and p.get("tier") in ("high", "medium")]
    if not today:
        today = [p for p in records if p.get("tier") == "high" and p.get("on_today_slate")]

    by_sport: dict[str, list] = defaultdict(list)
    meta: dict[tuple[str, str], dict] = {}
    for p in sorted(today, key=lambda x: -float(x.get("hit_rate", 0))):
        sport = str(p.get("sport", "?")).upper()
        if len(by_sport[sport]) >= limit:
            continue
        by_sport[sport].append(p)
        key = (_norm_name(p["player"]), sport)
        meta[key] = p

    keys = set(meta.keys())
    return keys, meta


def leg_matches_display_prop(leg: dict, player_meta: dict) -> bool:
    dp = player_meta.get("display_prop") or player_meta.get("best_prop")
    if not isinstance(dp, dict) or not dp.get("prop_type"):
        return False
    leg_prop = _prop_match_key(str(leg.get("prop_type") or ""))
    card_prop = _prop_match_key(str(dp.get("prop_type") or ""))
    if leg_prop != card_prop:
        return False
    leg_dir = str(leg.get("direction") or "").upper().strip()
    card_dir = str(dp.get("direction") or "").upper().strip()
    return leg_dir == card_dir


def grade_legs_for_date(slate_date: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _filter_payload_groups(payload, debug=False)
    merge_dates, _ = resolve_ticket_eval_graded_merge_dates(slate_date, payload, extra_iso_dates=None)
    indices = _load_actuals_indices(_dated_candidates(slate_date), merge_dates)

    graded: list[dict[str, Any]] = []
    for group in payload.get("groups") or []:
        gname = str(group.get("group_name") or "Group")
        for ticket in group.get("tickets") or []:
            ticket_id = str(ticket.get("ticket_id") or "")
            for leg in ticket.get("legs") or ticket.get("rows") or []:
                row = _match_leg_to_row_multi(leg, indices)
                line_f = _safe_float(leg.get("line"))
                if row and row.get("line") is not None and line_f is None:
                    line_f = _safe_float(row.get("line"))
                direction = str(leg.get("direction") or "").strip().upper()
                actual = row["actual"] if row else None
                graw = row["grade_raw"] if row else ""
                vnote = row.get("void_note", "") if row else ""
                grade = _leg_grade(actual, line_f, direction, graw, vnote)
                status, _ = _normalize_status_reason(grade, graw, vnote)
                sport = bpc._normalize_slate_sport(str(leg.get("sport") or ""))
                player = str(leg.get("player") or "").strip()
                lookup_sport = hot_lookup_sport(sport)
                graded.append(
                    {
                        "slate_date": slate_date,
                        "group_name": gname,
                        "ticket_id": ticket_id,
                        "player": player,
                        "sport": sport,
                        "prop_type": str(leg.get("prop_type") or ""),
                        "direction": direction,
                        "grade": grade,
                        "status": status,
                        "player_key": (_norm_name(player), lookup_sport),
                    }
                )
    return graded


def main() -> int:
    df, source = bpc.load_graded_dataframe()
    df = bpc.normalize_columns(df)
    if "file_date" not in df.columns and "game_date" in df.columns:
        df["file_date"] = df["game_date"]
    print(f"[hot-ticket-bt] Graded source: {source} ({len(df):,} rows)")

    tickets_by_date = discover_ticket_jsons()
    if not tickets_by_date:
        print("[hot-ticket-bt] No combined_slate_tickets_*.json found", file=sys.stderr)
        return 1

    all_legs: list[dict[str, Any]] = []
    hot_meta_by_date: dict[str, dict[tuple[str, str], dict]] = {}

    for slate_date, path in tickets_by_date.items():
        try:
            payload = _load_tickets(path, slate_date)
        except Exception as exc:
            print(f"  [skip] {slate_date}: load failed: {exc}")
            continue
        slate_players, slate_pairs = slate_pairs_from_payload(payload)
        hot_keys, meta = hot_player_keys_for_date(df, slate_date, slate_players, slate_pairs)
        hot_meta_by_date[slate_date] = meta
        legs = grade_legs_for_date(slate_date, payload)
        for leg in legs:
            leg["is_hot_player"] = leg["player_key"] in hot_keys
            pm = meta.get(leg["player_key"])
            leg["matches_card_prop"] = bool(pm and leg_matches_display_prop(leg, pm))
        all_legs.extend(legs)
        n_hot = sum(1 for x in legs if x["is_hot_player"])
        print(f"  {slate_date}: {len(legs)} legs, {n_hot} hot-player legs, {len(hot_keys)} featured players")

    if not all_legs:
        print("[hot-ticket-bt] No legs graded")
        return 1

    legs_df = pd.DataFrame(all_legs)
    decided = legs_df[legs_df["status"].isin(["HIT", "MISS"])].copy()

    def summarize(label: str, sub: pd.DataFrame) -> None:
        n = len(sub)
        hits = int((sub["status"] == "HIT").sum())
        rate = hits / n if n else 0.0
        print(f"  {label}: {hits}/{n} legs = {rate:.1%}")

    print("\n=== Leg hit rate (HIT vs MISS only) ===")
    summarize("All ticket legs", decided)
    summarize("Hot Player featured (any prop on ticket)", decided[decided["is_hot_player"]])
    summarize("Hot Player + same prop/direction as card", decided[decided["matches_card_prop"]])
    non_hot = decided[~decided["is_hot_player"]]
    summarize("Not on Hot Players list", non_hot)

    print("\n=== By sport (hot-player legs) ===")
    hot = decided[decided["is_hot_player"]]
    if not hot.empty:
        by = (
            hot.groupby("sport")
            .agg(legs=("status", "count"), hits=("status", lambda s: (s == "HIT").sum()))
            .assign(rate=lambda x: (x["hits"] / x["legs"]).round(3))
            .sort_values("legs", ascending=False)
        )
        print(by.to_string())

    print("\n=== Tickets with ≥1 hot-player leg (decided tickets) ===")
    # Ticket-level: any hot leg
    ticket_legs = decided.groupby(["slate_date", "ticket_id"])
    ticket_rows = []
    for (sd, tid), g in ticket_legs:
        statuses = list(g["status"])
        has_hot = bool(g["is_hot_player"].any())
        if not has_hot:
            continue
        n_hit = sum(1 for s in statuses if s == "HIT")
        n_miss = sum(1 for s in statuses if s == "MISS")
        all_hit = n_miss == 0 and n_hit == len(statuses) and n_hit > 0
        ticket_rows.append(
            {
                "slate_date": sd,
                "ticket_id": tid,
                "n_legs": len(statuses),
                "n_hit": n_hit,
                "n_miss": n_miss,
                "all_legs_hit": all_hit,
            }
        )
    if ticket_rows:
        tdf = pd.DataFrame(ticket_rows)
        n_tix = len(tdf)
        all_hit_n = int(tdf["all_legs_hit"].sum())
        print(f"  Tickets with ≥1 hot player leg: {n_tix}")
        print(f"  Those tickets went perfect (all legs HIT): {all_hit_n}/{n_tix} = {all_hit_n/n_tix:.1%}")
    else:
        print("  (no hot-player legs on decided tickets)")

    out_csv = REPO / "data" / "reports" / "hot_players_ticket_backtest.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    legs_df.to_csv(out_csv, index=False)
    print(f"\nWrote leg-level detail -> {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
