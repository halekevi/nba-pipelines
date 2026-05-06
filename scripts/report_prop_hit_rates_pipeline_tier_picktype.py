#!/usr/bin/env python3
"""
Pull graded_props_*.json (one file per slate date, newest path if duplicates),
then list prop-level hit rates grouped by pipeline (sport), tier, and pick type.

Usage:
  py -3 scripts/report_prop_hit_rates_pipeline_tier_picktype.py
  py -3 scripts/report_prop_hit_rates_pipeline_tier_picktype.py --from 2026-04-01 --min-decided 20
  py -3 scripts/report_prop_hit_rates_pipeline_tier_picktype.py --output data/reports/custom.csv
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


DATE_RE = re.compile(r"^graded_props_(\d{4}-\d{2}-\d{2})\.json$", re.I)


def _repo_root(cli: Path | None) -> Path:
    if cli is not None:
        return cli.resolve()
    return Path(__file__).resolve().parents[1]


def _norm(s: Any) -> str:
    t = str(s or "").strip()
    return t


def _norm_tier(s: Any) -> str:
    t = _norm(s).upper()
    if t in ("A", "B", "C", "D"):
        return t
    if not t:
        return "—"
    return t


def _norm_pick(s: Any) -> str:
    t = _norm(s)
    u = t.lower()
    if u in ("standard", "std"):
        return "Standard"
    if u == "goblin":
        return "Goblin"
    if u == "demon":
        return "Demon"
    return t or "—"


def _iter_resolved_json_paths(repo: Path) -> list[Path]:
    """One path per slate date: prefer the newer mtime between templates/ and mobile/www."""
    by_date: dict[str, Path] = {}
    bases = (
        repo / "ui_runner" / "templates",
        repo / "mobile" / "www",
    )
    for base in bases:
        if not base.is_dir():
            continue
        for p in base.glob("graded_props_*.json"):
            m = DATE_RE.match(p.name)
            if not m:
                continue
            d = m.group(1)
            prev = by_date.get(d)
            if prev is None or p.stat().st_mtime > prev.stat().st_mtime:
                by_date[d] = p
    return sorted(by_date.values(), key=lambda x: x.name)


def _load_props(paths: list[Path], from_date: str) -> Iterator[dict[str, Any]]:
    from_s = (from_date or "").strip()[:10]
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        file_date = str(raw.get("date") or "")[:10]
        if from_s and len(from_s) == 10 and file_date < from_s:
            continue
        for p in raw.get("props") or []:
            if isinstance(p, dict):
                yield dict(p, file_date=file_date)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prop hit rates by sport × prop × tier × pick type.")
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument(
        "--from",
        dest="from_date",
        default="",
        metavar="YYYY-MM-DD",
        help="Minimum slate date (inclusive).",
    )
    ap.add_argument(
        "--min-decided",
        type=int,
        default=1,
        help="Drop groups with fewer decided legs (default: 1).",
    )
    ap.add_argument(
        "--exclude-demon",
        action="store_true",
        help="Exclude pick_type Demon from aggregates.",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV path (default: data/reports/prop_hit_rates_pipeline_tier_picktype.csv).",
    )
    args = ap.parse_args()
    repo = _repo_root(args.repo_root)
    paths = _iter_resolved_json_paths(repo)
    if not paths:
        print("No graded_props_*.json found under ui_runner/templates or mobile/www.", file=sys.stderr)
        return 1

    agg: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(lambda: {"decided": 0, "hits": 0})

    for row in _load_props(paths, args.from_date):
        ru = _norm(row.get("result")).upper()
        if ru not in ("HIT", "MISS"):
            continue
        sport = _norm(row.get("sport")).upper() or "—"
        prop = _norm(row.get("prop")) or "—"
        tier = _norm_tier(row.get("tier"))
        pt = _norm_pick(row.get("pick_type"))
        if args.exclude_demon and pt == "Demon":
            continue
        key = (sport, prop, tier, pt)
        agg[key]["decided"] += 1
        if ru == "HIT":
            agg[key]["hits"] += 1

    rows_out: list[dict[str, Any]] = []
    for (sport, prop, tier, pt), v in agg.items():
        d = int(v["decided"])
        if d < args.min_decided:
            continue
        h = int(v["hits"])
        rows_out.append(
            {
                "sport": sport,
                "prop": prop,
                "tier": tier,
                "pick_type": pt,
                "decided": d,
                "hits": h,
                "misses": d - h,
                "hit_rate": round(h / d, 4) if d else 0.0,
            }
        )

    rows_out.sort(key=lambda r: (-r["decided"], -r["hit_rate"], r["sport"], r["prop"], r["tier"], r["pick_type"]))

    out_path = args.output
    if out_path is None:
        out_path = repo / "data" / "reports" / "prop_hit_rates_pipeline_tier_picktype.csv"
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import csv

    fields = ["sport", "prop", "tier", "pick_type", "decided", "hits", "misses", "hit_rate"]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows_out)

    print(f"Sources: {len(paths)} graded_props day files (newest per date).")
    print(f"Groups (sport × prop × tier × pick_type): {len(rows_out):,}  →  {out_path}")
    # Compact rollup: sport × tier × pick_type (still prop-level file is the main deliverable)
    roll: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"decided": 0, "hits": 0})
    for r in rows_out:
        k = (r["sport"], r["tier"], r["pick_type"])
        roll[k]["decided"] += r["decided"]
        roll[k]["hits"] += r["hits"]
    roll_rows = []
    for (sport, tier, pt), v in roll.items():
        d = v["decided"]
        h = v["hits"]
        roll_rows.append((sport, tier, pt, d, h, round(h / d, 4) if d else 0.0))
    roll_rows.sort(key=lambda x: (-x[3], -x[5], x[0], x[1], x[2]))
    print("\nTop 25 rollups (sport × tier × pick_type) by volume:")
    for sport, tier, pt, d, h, hr in roll_rows[:25]:
        print(f"  {sport:6}  tier {tier:3}  {pt:8}  n={d:5}  hit_rate={hr:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
