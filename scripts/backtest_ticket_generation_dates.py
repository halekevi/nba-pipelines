#!/usr/bin/env python3
"""
Backtest saved ticket snapshots (combined_slate_tickets_*) against archived actuals
using the current combined_ticket_grader logic, then summarize empirical ticket win rate.

Resolves paths the same way as scripts/run_grader.ps1:
  outputs/<date>[/canonical]/combined_slate_tickets_<date>[_to_grade_tomorrow].{xlsx,json}

Requires per date:
  outputs/<date>/actuals_nba_<date>.csv

Optional actuals (passed through when present):
  nba1h, nba1q, cbb (or wcbb as cbb), nhl, soccer, tennis

Examples:
  py scripts/backtest_ticket_generation_dates.py --dates 2026-02-15,2026-02-16 \\
    --stacked-csv training/graded_tickets_backtest.csv

  py scripts/backtest_ticket_generation_dates.py --from 2026-02-01 --to 2026-02-20 \\
    --stacked-csv training/graded_tickets_feb.csv
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from combined_ticket_grader import build_ticket_objective_decile_summary  # noqa: E402


def _parse_dates(s: str) -> list[str]:
    out: list[str] = []
    for part in str(s).split(","):
        p = part.strip()
        if not p:
            continue
        datetime.strptime(p, "%Y-%m-%d")  # validate
        out.append(p)
    return out


def _daterange(d0: str, d1: str) -> list[str]:
    a = datetime.strptime(d0, "%Y-%m-%d").date()
    b = datetime.strptime(d1, "%Y-%m-%d").date()
    if b < a:
        raise SystemExit("--to must be on or after --from")
    cur = a
    out: list[str] = []
    while cur <= b:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def resolve_tickets_file(outputs_root: Path, date_str: str) -> Optional[Path]:
    date_dir = outputs_root / date_str
    can = date_dir / "canonical"
    candidates = [
        can / f"combined_slate_tickets_{date_str}_to_grade_tomorrow.xlsx",
        date_dir / f"combined_slate_tickets_{date_str}_to_grade_tomorrow.xlsx",
        can / f"combined_slate_tickets_{date_str}.xlsx",
        date_dir / f"combined_slate_tickets_{date_str}.xlsx",
        can / f"combined_slate_tickets_{date_str}.json",
        date_dir / f"combined_slate_tickets_{date_str}.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def resolve_optional_actuals(date_dir: Path, date_str: str) -> dict[str, Path]:
    """Paths for grader optional args; 'cbb' prefers actuals_cbb then actuals_wcbb."""
    m: dict[str, Path] = {}
    pairs = [
        ("nba1h", f"actuals_nba1h_{date_str}.csv"),
        ("nba1q", f"actuals_nba1q_{date_str}.csv"),
        ("nhl", f"actuals_nhl_{date_str}.csv"),
        ("soccer", f"actuals_soccer_{date_str}.csv"),
        ("tennis", f"actuals_tennis_{date_str}.csv"),
    ]
    for key, name in pairs:
        p = date_dir / name
        if p.exists():
            m[key] = p
    cbb = date_dir / f"actuals_cbb_{date_str}.csv"
    wcbb = date_dir / f"actuals_wcbb_{date_str}.csv"
    if cbb.exists():
        m["cbb"] = cbb
    elif wcbb.exists():
        m["cbb"] = wcbb
    return m


def _normalize_include_col(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "include_in_ticket_rate" not in out.columns:
        return out
    ic = out["include_in_ticket_rate"]
    if ic.dtype == object or pd.api.types.is_string_dtype(ic):
        sl = ic.astype(str).str.strip().str.lower()
        out["include_in_ticket_rate"] = sl.isin(("true", "1", "t", "yes"))
    else:
        out["include_in_ticket_rate"] = ic.astype(bool)
    return out


def _print_per_date_win_rates(df: pd.DataFrame) -> None:
    df = _normalize_include_col(df)
    if "grade_date" not in df.columns:
        print("(no grade_date column; skipping per-date table)")
        return
    sub = df[df["include_in_ticket_rate"]].copy()
    sub = sub[pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce").notna()]
    if sub.empty:
        print("no decidable tickets with include_in_ticket_rate")
        return
    g = (
        sub.groupby("grade_date", dropna=False)
        .agg(
            n_tickets=("empirical_ticket_paid", "count"),
            win_rate=("empirical_ticket_paid", "mean"),
            mean_model_obj=("modeled_ticket_objective", "mean"),
        )
        .reset_index()
        .sort_values("grade_date")
    )
    g["win_rate"] = g["win_rate"].round(4)
    g["mean_model_obj"] = g["mean_model_obj"].round(4)
    print("\n--- PER-DATE TICKET WIN RATE (empirical_ticket_paid mean) ---")
    with pd.option_context("display.max_rows", 200, "display.width", 120):
        print(g.to_string(index=False))


def run_grader_subprocess(
    *,
    grader_py: Path,
    tickets: Path,
    nba_actuals: Path,
    optional: dict[str, Path],
    stacked_csv: Path,
    append_csv: bool,
    no_ml: bool,
    extra: list[str],
) -> None:
    cmd: list[str] = [sys.executable, str(grader_py), "--tickets", str(tickets), "--nba_actuals", str(nba_actuals)]
    if optional.get("nba1h"):
        cmd.extend(["--nba1h_actuals", str(optional["nba1h"])])
    if optional.get("nba1q"):
        cmd.extend(["--nba1q_actuals", str(optional["nba1q"])])
    if optional.get("cbb"):
        cmd.extend(["--cbb_actuals", str(optional["cbb"])])
    if optional.get("nhl"):
        cmd.extend(["--nhl_actuals", str(optional["nhl"])])
    if optional.get("soccer"):
        cmd.extend(["--soccer_actuals", str(optional["soccer"])])
    if optional.get("tennis"):
        cmd.extend(["--tennis_actuals", str(optional["tennis"])])
    if no_ml:
        cmd.append("--no-ml")
    cmd.extend(["--export-graded-tickets-csv", str(stacked_csv)])
    if append_csv:
        cmd.append("--append-graded-tickets-csv")
    cmd.extend(extra)
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"grader failed (exit {r.returncode}) for tickets={tickets}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch-grade historical ticket files and summarize win rates.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dates", help="Comma-separated YYYY-MM-DD")
    g.add_argument("--from", dest="date_from", help="Start date (with --to)")
    ap.add_argument("--to", dest="date_to", help="End date inclusive (with --from)")
    ap.add_argument(
        "--outputs-root",
        type=Path,
        default=ROOT / "outputs",
        help="Directory containing per-date folders (default: <repo>/outputs)",
    )
    ap.add_argument(
        "--stacked-csv",
        type=Path,
        required=True,
        help="Write merged graded-ticket rows here (overwritten unless --append-stack)",
    )
    ap.add_argument(
        "--append-stack",
        action="store_true",
        help="Append to --stacked-csv instead of truncating at start",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print planned commands only")
    ap.add_argument(
        "--with-ml",
        action="store_true",
        help="Run full grader including ML sheets (default: --no-ml for faster batch)",
    )
    ap.add_argument(
        "--grader-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="Extra argument to combined_ticket_grader.py (repeatable)",
    )
    args = ap.parse_args()

    if args.dates:
        dates = _parse_dates(args.dates)
    else:
        if not args.date_from or not args.date_to:
            raise SystemExit("use --dates or both --from and --to")
        dates = _daterange(args.date_from, args.date_to)

    stacked = Path(args.stacked_csv)
    if not args.append_stack and stacked.exists() and not args.dry_run:
        stacked.unlink()

    grader_py = SCRIPTS / "combined_ticket_grader.py"
    if not grader_py.exists():
        raise SystemExit(f"missing {grader_py}")

    no_ml = not args.with_ml
    append_graded = bool(
        args.append_stack and stacked.exists() and stacked.stat().st_size > 0
    )
    for d in dates:
        date_dir = args.outputs_root / d
        nba = date_dir / f"actuals_nba_{d}.csv"
        tix = resolve_tickets_file(args.outputs_root, d)
        if not tix:
            print(f"[skip] {d}: no combined_slate_tickets file under {date_dir}")
            continue
        if not nba.exists():
            print(f"[skip] {d}: missing {nba.name}")
            continue
        opt = resolve_optional_actuals(date_dir, d)
        if args.dry_run:
            print(
                f"[dry-run] {d} tickets={tix.name} nba_actuals=yes "
                f"optional={list(opt.keys())} append_graded_tickets_csv={append_graded}"
            )
            continue
        run_grader_subprocess(
            grader_py=grader_py,
            tickets=tix,
            nba_actuals=nba,
            optional=opt,
            stacked_csv=stacked,
            append_csv=append_graded,
            no_ml=no_ml,
            extra=list(args.grader_arg or []),
        )
        append_graded = True

    if args.dry_run:
        return

    if not stacked.exists():
        print("No graded ticket CSV produced (all dates skipped or no inputs).")
        return

    df = pd.read_csv(stacked)
    df = _normalize_include_col(df)
    need = {"modeled_ticket_objective", "empirical_ticket_paid", "include_in_ticket_rate"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"CSV missing columns {sorted(missing)}; got {list(df.columns)}")

    sub = df[df["include_in_ticket_rate"]].copy()
    sub = sub[pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce").notna()]
    paid = pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce")
    print(f"\nrows_total={len(df)} include_in_ticket_rate={len(sub)} overall_win_rate={paid.mean():.4f}")

    _print_per_date_win_rates(df)

    dec = build_ticket_objective_decile_summary(df)
    print("\n--- TICKET_OBJ_DECILES (stacked) ---")
    with pd.option_context("display.max_rows", 30, "display.width", 120):
        print(dec.to_string(index=False))

    try:
        from sklearn.metrics import roc_auc_score

        mo = pd.to_numeric(sub["modeled_ticket_objective"], errors="coerce")
        mask = mo.notna()
        y = paid.loc[mask].astype(float).values
        s = mo.loc[mask].astype(float).values
        if len(y) >= 10 and np.unique(y).size >= 2:
            auc = roc_auc_score(y, s)
            print(f"\nROC-AUC(modeled_ticket_objective vs paid): {auc:.4f} (n={len(y)})")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
