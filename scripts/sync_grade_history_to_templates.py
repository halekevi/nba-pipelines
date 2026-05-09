#!/usr/bin/env python3
"""
Copy the newest existing grade_history.json into ui_runner/templates/grade_history.json.

Run after build_ticket_eval.py so Railway/git deploys pick up fresh P&L rows when the app
falls back to the bundled template (no persistent volume).

  py -3.14 scripts/sync_grade_history_to_templates.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.proporacle_data_root import grade_history_read_paths  # noqa: E402


def main() -> int:
    td = ROOT / "ui_runner" / "templates"
    td.mkdir(parents=True, exist_ok=True)
    dest = td / "grade_history.json"
    candidates = grade_history_read_paths(ROOT, templates_dir=td)
    best: Path | None = None
    best_mtime = -1.0
    for p in candidates:
        if not p.is_file():
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > best_mtime:
            best_mtime = mt
            best = p
    if best is None:
        print("[sync_grade_history] no grade_history.json found (run build_ticket_eval.py first)")
        return 1
    shutil.copy2(best, dest)
    print(f"[sync_grade_history] {best} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
