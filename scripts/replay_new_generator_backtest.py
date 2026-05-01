#!/usr/bin/env python3
"""
Replay ticket generation for historical dates using date-stamped step files in
outputs/<date>/, then grade regenerated tickets with current grader logic.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _daterange(d0: str, d1: str) -> list[str]:
    a = datetime.strptime(d0, "%Y-%m-%d").date()
    b = datetime.strptime(d1, "%Y-%m-%d").date()
    if b < a:
        raise SystemExit("--to must be on or after --from")
    out: list[str] = []
    cur = a
    while cur <= b:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _latest_dates_with_actuals(outputs_root: Path, days: int) -> list[str]:
    vals: list[str] = []
    for p in sorted(outputs_root.glob("????-??-??"), reverse=True):
        d = p.name
        if (p / f"actuals_nba_{d}.csv").exists():
            vals.append(d)
        if len(vals) >= days:
            break
    return sorted(vals)


def _resolve_board(date_dir: Path, stem: str) -> Optional[Path]:
    exact = date_dir / f"{stem}.xlsx"
    if exact.exists():
        return exact
    matches = sorted(date_dir.glob(f"{stem}_*.xlsx"))
    return matches[-1] if matches else None


def _resolve_optional_actuals(date_dir: Path, d: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    map_names = {
        "nba1h": f"actuals_nba1h_{d}.csv",
        "nba1q": f"actuals_nba1q_{d}.csv",
        "nhl": f"actuals_nhl_{d}.csv",
        "soccer": f"actuals_soccer_{d}.csv",
        "tennis": f"actuals_tennis_{d}.csv",
    }
    for k, name in map_names.items():
        p = date_dir / name
        if p.exists():
            out[k] = p
    cbb = date_dir / f"actuals_cbb_{d}.csv"
    wcbb = date_dir / f"actuals_wcbb_{d}.csv"
    if cbb.exists():
        out["cbb"] = cbb
    elif wcbb.exists():
        out["cbb"] = wcbb
    return out


def _run(cmd: list[str]) -> None:
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    if rc != 0:
        raise SystemExit(f"command failed ({rc}): {' '.join(cmd)}")


def _normalize_include(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "include_in_ticket_rate" not in out.columns:
        return out
    s = out["include_in_ticket_rate"]
    if s.dtype == object:
        out["include_in_ticket_rate"] = s.astype(str).str.strip().str.lower().isin(("1", "true", "t", "yes"))
    else:
        out["include_in_ticket_rate"] = s.astype(bool)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay historical generation + grade regenerated tickets.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--last-n", type=int, help="Most recent N dates with nba actuals")
    g.add_argument("--from", dest="date_from", help="Start YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="End YYYY-MM-DD (required with --from)")
    ap.add_argument("--outputs-root", type=Path, default=ROOT / "outputs")
    ap.add_argument("--stacked-csv", type=Path, required=True, help="Stacked graded ticket CSV output path")
    ap.add_argument("--append-stack", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-ml", action="store_true", help="Run grader ML sheets (slower)")
    ap.add_argument("--max-tickets", type=int, default=4, help="Generator max tickets per group (default 4)")
    ap.add_argument("--ticket-gen-starts", type=int, default=1, help="Generator seed starts (default 1 for speed)")
    ap.add_argument(
        "--nba-structured-variants",
        type=int,
        default=1,
        help="Generator NBA structured variants (default 1 for speed)",
    )
    args = ap.parse_args()

    if args.last_n:
        dates = _latest_dates_with_actuals(args.outputs_root, int(args.last_n))
    else:
        if not args.date_from or not args.date_to:
            raise SystemExit("use --last-n or both --from and --to")
        dates = _daterange(args.date_from, args.date_to)
    if not dates:
        raise SystemExit("no dates selected")

    stacked = Path(args.stacked_csv)
    if stacked.exists() and not args.append_stack and not args.dry_run:
        stacked.unlink()
    append_export = bool(args.append_stack and stacked.exists() and stacked.stat().st_size > 0)

    generator = SCRIPTS / "combined_slate_tickets.py"
    grader = SCRIPTS / "combined_ticket_grader.py"

    for d in dates:
        date_dir = args.outputs_root / d
        nba_actuals = date_dir / f"actuals_nba_{d}.csv"
        if not nba_actuals.exists():
            print(f"[skip] {d}: missing {nba_actuals.name}")
            continue

        regen_xlsx = date_dir / f"combined_slate_tickets_{d}_REGEN.xlsx"

        boards = {
            "nba": _resolve_board(date_dir, f"step8_nba_direction_clean_{d}"),
            "nhl": _resolve_board(date_dir, f"step8_nhl_direction_clean_{d}"),
            "soccer": _resolve_board(date_dir, f"step8_soccer_direction_clean_{d}"),
            "tennis": _resolve_board(date_dir, f"step8_tennis_direction_clean_{d}"),
            "mlb": _resolve_board(date_dir, f"step8_mlb_direction_clean_{d}"),
            "nba1q": _resolve_board(date_dir, f"step8_nba1q_direction_clean_{d}"),
            "nba1h": _resolve_board(date_dir, f"step8_nba1h_direction_clean_{d}"),
        }

        gen_cmd = [
            sys.executable,
            str(generator),
            "--date",
            d,
            "--output",
            str(regen_xlsx),
            "--no-ticket-model-rerank",
            "--tiers",
            "A",
            "--pick-types",
            "Standard",
            "--max-ticket-legs",
            "3",
            "--prioritize-ticket-hit",
            "--allow-cross-date-fallback",
            "--max-tickets",
            str(int(args.max_tickets)),
            "--ticket-gen-starts",
            str(int(args.ticket_gen_starts)),
            "--nba-structured-variants",
            str(int(args.nba_structured_variants)),
        ]
        for key, p in boards.items():
            if p:
                gen_cmd.extend([f"--{key}", str(p)])

        optional_actuals = _resolve_optional_actuals(date_dir, d)
        grade_cmd = [
            sys.executable,
            str(grader),
            "--tickets",
            str(regen_xlsx),
            "--nba_actuals",
            str(nba_actuals),
            "--export-graded-tickets-csv",
            str(stacked),
        ]
        if not args.with_ml:
            grade_cmd.append("--no-ml")
        if append_export:
            grade_cmd.append("--append-graded-tickets-csv")
        if optional_actuals.get("nba1h"):
            grade_cmd.extend(["--nba1h_actuals", str(optional_actuals["nba1h"])])
        if optional_actuals.get("nba1q"):
            grade_cmd.extend(["--nba1q_actuals", str(optional_actuals["nba1q"])])
        if optional_actuals.get("cbb"):
            grade_cmd.extend(["--cbb_actuals", str(optional_actuals["cbb"])])
        if optional_actuals.get("nhl"):
            grade_cmd.extend(["--nhl_actuals", str(optional_actuals["nhl"])])
        if optional_actuals.get("soccer"):
            grade_cmd.extend(["--soccer_actuals", str(optional_actuals["soccer"])])
        if optional_actuals.get("tennis"):
            grade_cmd.extend(["--tennis_actuals", str(optional_actuals["tennis"])])

        print(f"[run] {d} regenerate -> {regen_xlsx.name}")
        if args.dry_run:
            print("  GEN:", " ".join(gen_cmd))
            print("  GRD:", " ".join(grade_cmd))
            append_export = True
            continue
        _run(gen_cmd)
        _run(grade_cmd)
        append_export = True

    if args.dry_run:
        return
    if not stacked.exists():
        raise SystemExit("no stacked csv produced")

    df = pd.read_csv(stacked)
    df = _normalize_include(df)
    sub = df[df["include_in_ticket_rate"]].copy()
    sub = sub[pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce").notna()]
    print(
        f"\nrows_total={len(df)} include_in_ticket_rate={len(sub)} "
        f"overall_win_rate={pd.to_numeric(sub['empirical_ticket_paid'], errors='coerce').mean():.4f}"
    )
    if "grade_date" in sub.columns and not sub.empty:
        gdf = (
            sub.groupby("grade_date", dropna=False)
            .agg(
                n_tickets=("empirical_ticket_paid", "count"),
                win_rate=("empirical_ticket_paid", "mean"),
                mean_model_obj=("modeled_ticket_objective", "mean"),
            )
            .reset_index()
            .sort_values("grade_date")
        )
        gdf["win_rate"] = gdf["win_rate"].round(4)
        gdf["mean_model_obj"] = gdf["mean_model_obj"].round(4)
        print("\n--- PER-DATE REGEN WIN RATES ---")
        with pd.option_context("display.max_rows", 200, "display.width", 120):
            print(gdf.to_string(index=False))


if __name__ == "__main__":
    main()
