#!/usr/bin/env python3
"""
Export props_history rows for one grade_date into ui_runner/data/grades_props/YYYY-MM-DD.json.

Use after local grading + step_archive so Railway (no data/cache/*.db in deploy) can still
serve /api/grades/props for bundled dates.

  py -3.14 scripts/export_grades_props_bundle.py --date 2026-04-04
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.prop_reconcile import reconcile_props_history_dict
CACHE = REPO / "data" / "cache"
OUT_DIR = REPO / "ui_runner" / "data" / "grades_props"


def _ensure_void_reason_column(conn: sqlite3.Connection) -> None:
    try:
        cur = conn.execute("PRAGMA table_info(props_history)")
        cols = {r[1] for r in cur.fetchall()}
        if cols and "void_reason" not in cols:
            conn.execute("ALTER TABLE props_history ADD COLUMN void_reason TEXT")
            conn.commit()
    except Exception:
        pass


def _json_val(v):
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def main() -> None:
    ap = argparse.ArgumentParser(description="Export props_history to bundled JSON for deploy.")
    ap.add_argument("--date", required=True, help="Grade date YYYY-MM-DD")
    args = ap.parse_args()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", args.date.strip()[:10])
    if not m:
        raise SystemExit("Invalid --date; use YYYY-MM-DD")
    date_str = m.group(1)

    props: list[dict] = []
    for dbp in sorted(CACHE.glob("*_props_history.db")):
        try:
            conn = sqlite3.connect(str(dbp))
            _ensure_void_reason_column(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT sport, grade_date, player_name, prop_type, line, direction,
                       actual_value, result, margin, opp_team, team, pick_type, tier, edge, ml_prob,
                       void_reason
                FROM props_history
                WHERE grade_date = ?
                ORDER BY sport, player_name, prop_type, direction
                """,
                (date_str,),
            )
            for row in cur.fetchall():
                item = {k: _json_val(row[k]) for k in row.keys()}
                props.append(reconcile_props_history_dict(item))
            conn.close()
        except Exception as exc:
            print(f"[warn] {dbp.name}: {exc}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{date_str}.json"
    payload = {"date": date_str, "props": props, "n": len(props)}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(props)} rows -> {out_path}")


if __name__ == "__main__":
    main()
