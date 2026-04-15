#!/usr/bin/env python3
"""
Ingest graded_props_*.json into proporacle_income.db (bet_result + related rows).

The income dashboard reads proporacle_income.db (see proporacle/data/schema/*.sql).
This script maps each decided prop (HIT/MISS) with ml_prob into the normalized
schema so v_roi_daily / v_calibration_bins reflect real grades instead of demo-only.

Idempotent per slate: slate_id = graded_props_{YYYY-MM-DD}. Re-run replaces rows for
those slates (DELETE then INSERT).

Usage:
  py -3.14 scripts/ingest_graded_to_income_db.py --date 2026-04-13
  py -3.14 scripts/ingest_graded_to_income_db.py --all
  py -3.14 scripts/ingest_graded_to_income_db.py --all --purge-demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from proporacle.monitoring.dashboard_queries import load_income_db  # noqa: E402

MODEL_VERSION = "graded_props_ingest_v1"
PRICING_VERSION = "graded_props_json"
SPORT_SLATE = "MULTI"


def _templates_dir() -> Path:
    return _REPO / "ui_runner" / "templates"


def _graded_json_paths(for_date: str | None) -> list[Path]:
    td = _templates_dir()
    if not td.is_dir():
        return []
    paths = sorted(td.glob("graded_props_*.json"))
    if for_date:
        exact = td / f"graded_props_{for_date}.json"
        return [exact] if exact.is_file() else []
    return paths


def _parse_date_from_path(p: Path) -> str | None:
    m = re.match(r"graded_props_(\d{4}-\d{2}-\d{2})\.json$", p.name)
    return m.group(1) if m else None


def _market_id(grade_date: str, sport: str, player: str, prop: str, line: str, direction: str) -> str:
    key = f"{grade_date}|{sport}|{player}|{prop}|{line}|{direction}".lower()
    return "m_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:28]


def _float_or_none(x) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_ev_fraction(edge_val: float | None) -> float:
    """Match grades-insights: large |edge| treated as percentage points."""
    if edge_val is None:
        return 0.02
    ef = float(edge_val)
    if abs(ef) > 1.5:
        ef = ef / 100.0
    return max(-0.5, min(0.5, ef))


def _purge_demo_slates(conn) -> int:
    cur = conn.cursor()
    n = 0
    for prefix in ("demo_slate_%",):
        cur.execute("SELECT slate_id FROM slate_run WHERE slate_id LIKE ?", (prefix,))
        ids = [r[0] for r in cur.fetchall()]
        for sid in ids:
            cur.execute("DELETE FROM bet_result WHERE slate_id = ?", (sid,))
            cur.execute("DELETE FROM bet_recommendation WHERE slate_id = ?", (sid,))
            cur.execute("DELETE FROM bet_candidate WHERE slate_id = ?", (sid,))
            cur.execute("DELETE FROM prediction WHERE slate_id = ?", (sid,))
            cur.execute("DELETE FROM slate_run WHERE slate_id = ?", (sid,))
            n += 1
    conn.commit()
    return n


def _delete_graded_props_slates(conn, slate_ids: list[str]) -> None:
    cur = conn.cursor()
    for sid in slate_ids:
        cur.execute("DELETE FROM bet_result WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM bet_recommendation WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM bet_candidate WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM prediction WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM slate_run WHERE slate_id = ?", (sid,))
    conn.commit()


def _ensure_model_version(conn) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO model_version (model_version, sport, trained_from, trained_to, n_train) "
        "VALUES (?, 'nba', '2020-01-01', '2030-01-01', 0)",
        (MODEL_VERSION,),
    )
    conn.commit()


def ingest_file(conn, path: Path) -> tuple[int, str | None]:
    grade_date = _parse_date_from_path(path)
    if not grade_date:
        return 0, "skip: bad filename"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return 0, f"read error: {e}"
    props = data.get("props") or []
    slate_id = f"graded_props_{grade_date}"
    inserted = 0

    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO slate_run (slate_id, sport, slate_date, status) VALUES (?,?,?, 'ingested')",
        (slate_id, SPORT_SLATE, grade_date),
    )

    for p in props:
        res = str(p.get("result") or "").strip().upper()
        if res not in ("HIT", "MISS"):
            continue
        ml = _float_or_none(p.get("ml_prob"))
        if ml is None:
            continue
        if ml > 1.0:
            ml = ml / 100.0
        ml = max(0.01, min(0.99, ml))

        sport = str(p.get("sport") or "UNK").strip() or "UNK"
        player = str(p.get("player") or p.get("player_name") or "").strip() or "unknown"
        prop = str(p.get("prop") or p.get("prop_type") or "").strip() or "prop"
        line = str(p.get("line") or "").strip()
        direction = str(p.get("direction") or "").strip() or "OVER"
        edge_raw = _float_or_none(p.get("edge"))
        ev = _norm_ev_fraction(edge_raw)

        mid = _market_id(grade_date, sport, player, prop, line, direction)
        settled = f"{grade_date}T23:59:00"

        pnl = (100.0 / 110.0) if res == "HIT" else -1.0

        cur.execute(
            "INSERT OR REPLACE INTO prediction (slate_id, market_id, p_raw, p_calibrated, model_version) "
            "VALUES (?,?,?,?,?)",
            (slate_id, mid, ml, ml, MODEL_VERSION),
        )
        cur.execute(
            "INSERT OR REPLACE INTO bet_candidate (slate_id, market_id, p_fair, p_implied, ev, edge_quality, "
            "american_odds, pricing_version) VALUES (?,?,?,?,?,?, -110, ?)",
            (slate_id, mid, ml, 0.524, ev, 0.1, PRICING_VERSION),
        )
        cur.execute(
            "INSERT OR REPLACE INTO bet_recommendation (slate_id, market_id, stake, model_version, pricing_version) "
            "VALUES (?,?,1.0,?,?)",
            (slate_id, mid, MODEL_VERSION, PRICING_VERSION),
        )
        cur.execute(
            "INSERT OR REPLACE INTO bet_result (slate_id, market_id, result, pnl_units, american_odds_open, "
            "american_odds_close, clv_implied_delta, settled_at) VALUES (?,?,?,?,?,?,?,?)",
            (slate_id, mid, res, pnl, -110, -110, None, settled),
        )
        inserted += 1

    conn.commit()
    return inserted, None


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest graded_props JSON into proporacle_income.db")
    ap.add_argument("--date", help="Single grade date YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="Process all graded_props_*.json in ui_runner/templates")
    ap.add_argument("--purge-demo", action="store_true", help="Remove demo_slate_* rows before ingest")
    args = ap.parse_args()

    if bool(args.date) == bool(args.all) and not (args.date or args.all):
        ap.error("Specify --date YYYY-MM-DD or --all")

    paths = _graded_json_paths(args.date if args.date else None)
    if args.all:
        paths = _graded_json_paths(None)
    if not paths:
        print("No graded_props_*.json files found.", file=sys.stderr)
        sys.exit(1)

    conn = load_income_db()
    try:
        _ensure_model_version(conn)
        if args.purge_demo:
            n = _purge_demo_slates(conn)
            print(f"Purged {n} demo slate(s).")
        slate_ids = list({f"graded_props_{_parse_date_from_path(p)}" for p in paths if _parse_date_from_path(p)})
        _delete_graded_props_slates(conn, slate_ids)

        total = 0
        for p in paths:
            n, err = ingest_file(conn, p)
            if err:
                print(f"{p.name}: {err}")
            else:
                print(f"{p.name}: inserted {n} bet_result row(s)")
                total += n
        print(f"Done. Total HIT/MISS rows with ml_prob: {total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
