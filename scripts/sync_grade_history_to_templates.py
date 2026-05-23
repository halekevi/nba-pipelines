#!/usr/bin/env python3
"""
Copy the newest existing grade_history.json into ui_runner/templates/grade_history.json.

Run after build_ticket_eval.py so Railway/git deploys pick up fresh P&L rows when the app
falls back to the bundled template (no persistent volume).

  py -3.14 scripts/sync_grade_history_to_templates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.proporacle_data_root import load_best_grade_history_runs  # noqa: E402


def main() -> int:
    td = ROOT / "ui_runner" / "templates"
    td.mkdir(parents=True, exist_ok=True)
    dest = td / "grade_history.json"
    runs = load_best_grade_history_runs(ROOT, templates_dir=td)
    if not runs:
        print("[sync_grade_history] no grade_history.json found (run build_ticket_eval.py first)")
        return 1
    payload = json.dumps(runs, indent=2, ensure_ascii=False) + "\n"
    try:
        dest.write_text(payload, encoding="utf-8")
    except OSError:
        print(f"[sync_grade_history] could not write {dest}")
        return 1
    last = str(runs[-1].get("date") or "") if runs else ""
    print(f"[sync_grade_history] wrote {len(runs)} rows (last={last}) -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
