#!/usr/bin/env python3
"""
Compare every known grade_history.json copy (sums, row counts, duplicate dates),
then optionally copy one file into ui_runner/templates/grade_history.json for Railway deploys.

  py -3.14 scripts/verify_and_sync_grade_history.py
  py -3.14 scripts/verify_and_sync_grade_history.py --apply --from mobile
  py -3.14 scripts/verify_and_sync_grade_history.py --apply --from data --also-data

``sync_grade_history_to_templates.py`` only considers persistent / data / templates (newest mtime).
This script also checks mobile/www/data/grade_history.json so you can align the bundled template
with the mobile bundle explicitly.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.proporacle_data_root import grade_history_read_paths, persistent_data_dir  # noqa: E402


def _load_runs(raw: object) -> list[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("runs"), list):
        return [x for x in (raw.get("runs") or []) if isinstance(x, dict)]
    return []


def _row_stats(path: Path) -> tuple[int, int, str, str, list[str]] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    runs = _load_runs(raw)
    total = 0
    dates: list[str] = []
    for r in runs:
        d = str(r.get("date") or "").strip()[:10]
        if not d:
            continue
        n = r.get("n_tickets", r.get("tickets", 0))
        try:
            total += max(0, int(n))
        except (TypeError, ValueError):
            pass
        dates.append(d)
    c = Counter(dates)
    dups = sorted(d for d, n in c.items() if n > 1)
    ds = sorted(set(dates))
    dr = f"{ds[0]} … {ds[-1]}" if ds else "—"
    return len(runs), total, dr, str(len(ds)), dups


def _all_candidate_paths() -> list[tuple[str, Path]]:
    td = ROOT / "ui_runner" / "templates"
    out: list[tuple[str, Path]] = [
        ("persistent", persistent_data_dir(ROOT) / "grade_history.json"),
        ("data", ROOT / "data" / "grade_history.json"),
        ("mobile_www_data", ROOT / "mobile" / "www" / "data" / "grade_history.json"),
        ("templates", td / "grade_history.json"),
    ]
    seen: set[str] = set()
    uniq: list[tuple[str, Path]] = []
    for label, p in out:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append((label, p))
    return uniq


def _print_report() -> list[tuple[str, Path, float]]:
    rows: list[tuple[str, Path, float]] = []
    print(f"{'label':<18} {'exists':>6} {'entries':>8} {'sum n_tickets':>14} {'unique dates':>13} {'range':<24} dups")
    for label, path in _all_candidate_paths():
        if not path.is_file():
            print(f"{label:<18} {'no':>6} {'—':>8} {'—':>14} {'—':>13} {'—':<24} —")
            continue
        try:
            mt = path.stat().st_mtime
        except OSError:
            mt = -1.0
        st = _row_stats(path)
        if st is None:
            print(f"{label:<18} {'bad':>6} {'—':>8} {'—':>14} {'—':>13} {'—':<24} —")
            continue
        n_ent, total, dr, nu, dups = st
        dup_s = ",".join(dups) if dups else "—"
        print(f"{label:<18} {'yes':>6} {n_ent:>8} {total:>14} {nu:>13} {dr:<24} {dup_s}")
        rows.append((label, path, mt))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--from",
        dest="from_src",
        choices=("newest", "mobile", "data", "templates", "persistent"),
        default="newest",
        help="Which file to copy when using --apply (default: newest mtime among existing files).",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Copy the chosen source into ui_runner/templates/grade_history.json",
    )
    ap.add_argument(
        "--also-data",
        action="store_true",
        help="After --apply, also copy the same source to data/grade_history.json",
    )
    args = ap.parse_args()

    print(f"ROOT={ROOT}\n")
    indexed = _print_report()

    if not args.apply:
        print("\nDry run only. Pass --apply --from <source> to update the bundled template.")
        return 0

    by_label = {label: (path, mt) for label, path, mt in indexed}
    src: Path | None = None
    src_label = ""

    if args.from_src == "newest":
        best_mt = -1.0
        for label, path, mt in indexed:
            if mt > best_mt:
                best_mt = mt
                src = path
                src_label = label
        if src is None:
            print("[verify_and_sync] no readable grade_history.json found.")
            return 1
    else:
        key = args.from_src
        if key == "mobile":
            key = "mobile_www_data"
        pair = by_label.get(key)
        if not pair:
            print(f"[verify_and_sync] --from {args.from_src}: file missing or unreadable.")
            return 1
        src, _mt = pair
        src_label = key

    dest_t = ROOT / "ui_runner" / "templates" / "grade_history.json"
    dest_t.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dest_t)
    except OSError:
        dest_t.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\n[verify_and_sync] wrote templates bundle: {src} ({src_label}) -> {dest_t}")

    if args.also_data:
        dest_d = ROOT / "data" / "grade_history.json"
        dest_d.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dest_d)
        except OSError:
            dest_d.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[verify_and_sync] wrote data copy: {src} -> {dest_d}")

    st = _row_stats(dest_t)
    if st:
        n_ent, total, dr, nu, dups = st
        print(f"[verify_and_sync] verify templates: entries={n_ent} sum_n_tickets={total} dates={nu} range={dr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
