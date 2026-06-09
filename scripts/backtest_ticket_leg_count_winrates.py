#!/usr/bin/env python3
"""
Backtest ticket win rates split by parlay size: main (2-leg win-rate) vs long (5-6 leg).

Uses saved combined_slate_tickets JSON, splits with the same logic as combined_slate_tickets.py,
grades via combined_ticket_grader.py (PrizePicks pay rules), and summarizes empirical_ticket_paid.

Example:
  python scripts/backtest_ticket_leg_count_winrates.py --from 2026-05-05 --to 2026-06-09
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from combined_slate_tickets import split_graded_ticket_payloads  # noqa: E402
from backtest_ticket_generation_dates import (  # noqa: E402
    resolve_optional_actuals,
    resolve_tickets_file,
    run_grader_subprocess,
)


def _daterange(d0: str, d1: str) -> list[str]:
    a = datetime.strptime(d0, "%Y-%m-%d").date()
    b = datetime.strptime(d1, "%Y-%m-%d").date()
    cur = a
    out: list[str] = []
    while cur <= b:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _resolve_ticket_json(date_str: str, outputs_root: Path) -> Path | None:
    ui = ROOT / "ui_runner" / "data" / f"combined_slate_tickets_{date_str}.json"
    if ui.is_file():
        return ui
    return resolve_tickets_file(outputs_root, date_str)


def _write_temp_payload(payload: dict, tmpdir: Path, date_str: str, label: str) -> Path:
    path = tmpdir / f"combined_slate_tickets_{label}_{date_str}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _summarize(df: pd.DataFrame) -> dict:
    sub = df.copy()
    if "include_in_ticket_rate" in sub.columns:
        ic = sub["include_in_ticket_rate"]
        if ic.dtype == object or pd.api.types.is_string_dtype(ic):
            sl = ic.astype(str).str.strip().str.lower()
            sub = sub[sl.isin(("true", "1", "t", "yes"))]
        else:
            sub = sub[ic.astype(bool)]
    sub = sub[pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce").notna()]
    if sub.empty:
        return {"n_tickets": 0, "win_rate": None, "wins": 0, "losses": 0}
    paid = pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce")
    wins = int((paid >= 0.5).sum())
    losses = int(len(paid) - wins)
    return {
        "n_tickets": int(len(paid)),
        "win_rate": round(float(paid.mean()), 4),
        "wins": wins,
        "losses": losses,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest 2-leg main vs 5-6 leg long-parlay ticket win rates.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dates", help="Comma-separated YYYY-MM-DD")
    g.add_argument("--from", dest="date_from", help="Start date (with --to)")
    ap.add_argument("--to", dest="date_to", help="End date inclusive")
    ap.add_argument(
        "--outputs-root",
        type=Path,
        default=ROOT / "outputs",
        help="outputs/<date>/ folder root",
    )
    ap.add_argument(
        "--out-json",
        type=Path,
        default=ROOT / "data" / "reports" / "ticket_leg_count_backtest.json",
    )
    args = ap.parse_args()

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        if not args.date_from or not args.date_to:
            ap.error("use --dates or both --from and --to")
        dates = _daterange(args.date_from, args.date_to)

    grader_py = SCRIPTS / "combined_ticket_grader.py"
    if not grader_py.is_file():
        print(f"ERROR: missing {grader_py}")
        return 1

    stacked_rows: list[pd.DataFrame] = []
    per_date_meta: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="legcount_bt_") as tmp:
        tmpdir = Path(tmp)
        stacked_csv = tmpdir / "stacked.csv"

        for d in dates:
            date_dir = args.outputs_root / d
            nba = date_dir / f"actuals_nba_{d}.csv"
            tix = _resolve_ticket_json(d, args.outputs_root)
            if not tix:
                print(f"[skip] {d}: no ticket JSON")
                continue
            if not nba.is_file():
                print(f"[skip] {d}: missing {nba.name}")
                continue

            try:
                full = json.loads(tix.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[skip] {d}: bad JSON ({exc})")
                continue

            main_p, long_p = split_graded_ticket_payloads(full)
            opt = resolve_optional_actuals(date_dir, d)
            append = stacked_csv.is_file() and stacked_csv.stat().st_size > 0

            buckets = [
                ("main_2_4", main_p),
                ("long_5_6", long_p),
            ]
            date_counts = {
                "all": sum(len(g.get("tickets") or []) for g in full.get("groups") or []),
                "main_2_4": sum(len(g.get("tickets") or []) for g in main_p.get("groups") or []),
                "long_5_6": sum(len(g.get("tickets") or []) for g in long_p.get("groups") or []),
            }
            for label, payload in buckets:
                if date_counts[label] == 0:
                    continue
                temp_path = _write_temp_payload(payload, tmpdir, d, label)
                run_grader_subprocess(
                    grader_py=grader_py,
                    tickets=temp_path,
                    nba_actuals=nba,
                    optional=opt,
                    stacked_csv=stacked_csv,
                    append_csv=append,
                    no_ml=True,
                    extra=[],
                )
                append = True

            per_date_meta.append({"date": d, "ticket_source": tix.name, **date_counts})
            print(
                f"[ok] {d}: all={date_counts.get('all', 0)} "
                f"main_2_4={date_counts.get('main_2_4', 0)} "
                f"long_5_6={date_counts.get('long_5_6', 0)}"
            )

        if not stacked_csv.is_file():
            print("No graded rows produced.")
            return 1

        df = pd.read_csv(stacked_csv)
        if "source_workbook" not in df.columns:
            print("ERROR: grader CSV missing source_workbook column")
            return 1

        def _bucket_from_source(name: str) -> str:
            low = str(name or "").lower()
            if "main_2_4" in low:
                return "main_2_4"
            if "long_5_6" in low:
                return "long_5_6"
            return "all"

        df["leg_bucket"] = df["source_workbook"].map(_bucket_from_source)

        # "all" = union of split buckets (same slips as full export when split is exhaustive).
        summary_by_bucket: dict[str, dict] = {}
        for bucket in ("main_2_4", "long_5_6"):
            summary_by_bucket[bucket] = _summarize(df[df["leg_bucket"] == bucket])
        summary_by_bucket["all"] = _summarize(df)

        by_n_legs: dict[str, dict] = {}
        sub = df[df["include_in_ticket_rate"]].copy()
        sub = sub[pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce").notna()]
        if not sub.empty and "n_legs" in sub.columns:
            for n, g in sub.groupby("n_legs"):
                paid = pd.to_numeric(g["empirical_ticket_paid"], errors="coerce")
                by_n_legs[str(int(n))] = {
                    "n_tickets": int(len(paid)),
                    "win_rate": round(float(paid.mean()), 4),
                }

        per_date: list[dict] = []
        for d in sorted(sub["grade_date"].dropna().unique()):
            for bucket in ("all", "main_2_4", "long_5_6"):
                mask = (sub["grade_date"] == d) & (sub["leg_bucket"] == bucket)
                if not mask.any():
                    continue
                paid = pd.to_numeric(sub.loc[mask, "empirical_ticket_paid"], errors="coerce")
                per_date.append(
                    {
                        "date": str(d),
                        "leg_bucket": bucket,
                        "n_tickets": int(len(paid)),
                        "win_rate": round(float(paid.mean()), 4),
                    }
                )

        report = {
            "generated_at_utc": datetime.utcnow().isoformat() + "Z",
            "date_range": {"from": dates[0], "to": dates[-1], "n_dates_attempted": len(dates)},
            "summary_by_bucket": summary_by_bucket,
            "by_n_legs": by_n_legs,
            "per_date": per_date,
            "slates": per_date_meta,
        }

        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.out_json}")

        print("\n=== SUMMARY BY LEG BUCKET ===")
        for bucket, s in summary_by_bucket.items():
            wr = s["win_rate"]
            wr_s = f"{100.0 * wr:.1f}%" if wr is not None else "n/a"
            print(f"  {bucket:10s}  n={s['n_tickets']:5d}  win_rate={wr_s}")

        print("\n=== BY LEG COUNT ===")
        for n in sorted(by_n_legs.keys(), key=lambda x: int(x)):
            s = by_n_legs[n]
            print(f"  {n}-leg  n={s['n_tickets']:5d}  win_rate={100.0 * s['win_rate']:.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
