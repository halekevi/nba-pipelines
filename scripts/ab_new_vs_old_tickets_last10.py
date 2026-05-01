#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _run(cmd: list[str]) -> None:
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    if rc != 0:
        raise RuntimeError(f"command failed ({rc}): {' '.join(cmd)}")


def _last10_dates(outputs_root: Path) -> list[str]:
    dates: list[str] = []
    for p in sorted(outputs_root.glob("????-??-??"), reverse=True):
        d = p.name
        if d >= "2090-01-01":
            continue
        if (p / f"actuals_nba_{d}.csv").exists():
            dates.append(d)
        if len(dates) >= 10:
            break
    return sorted(dates)


def _resolve_board(date_dir: Path, stem: str) -> Path | None:
    p = date_dir / f"{stem}.xlsx"
    if p.exists():
        return p
    cands = sorted(date_dir.glob(f"{stem}_*.xlsx"))
    return cands[-1] if cands else None


def _optional_actuals(date_dir: Path, d: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for k, name in (
        ("nba1h", f"actuals_nba1h_{d}.csv"),
        ("nba1q", f"actuals_nba1q_{d}.csv"),
        ("nhl", f"actuals_nhl_{d}.csv"),
        ("soccer", f"actuals_soccer_{d}.csv"),
        ("tennis", f"actuals_tennis_{d}.csv"),
    ):
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


def _read_ticket_results(xlsx: Path, label: str, d: str) -> pd.DataFrame:
    if not xlsx.exists():
        return pd.DataFrame()
    try:
        tr = pd.read_excel(xlsx, sheet_name="TICKET_RESULTS")
    except Exception:
        return pd.DataFrame()
    if tr.empty:
        return pd.DataFrame()
    tr = tr.copy()
    tr["mode"] = tr.get("mode", "").astype(str).str.lower().str.strip()
    tr["profit"] = pd.to_numeric(tr.get("profit"), errors="coerce")
    tr["is_cash"] = pd.to_numeric(tr.get("is_cash"), errors="coerce")
    tr["grade_date"] = d
    tr["arm"] = label
    return tr[["grade_date", "arm", "mode", "profit", "is_cash"]]


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B compare old tickets vs new constrained combined-slate replay on last 10 days.")
    ap.add_argument("--outputs-root", type=Path, default=ROOT / "outputs")
    ap.add_argument("--out-csv", type=Path, default=ROOT / "outputs" / "_ab_new_vs_old_last10.csv")
    args = ap.parse_args()

    dates = _last10_dates(args.outputs_root)
    if not dates:
        raise SystemExit("No candidate dates found.")
    print("dates:", ",".join(dates))

    rows: list[pd.DataFrame] = []
    for d in dates:
        date_dir = args.outputs_root / d
        nba_actuals = date_dir / f"actuals_nba_{d}.csv"
        if not nba_actuals.exists():
            print(f"[skip] {d} no nba actuals")
            continue

        # Old arm: existing graded workbook if present.
        old_candidates = [
            date_dir / f"combined_tickets_graded_{d}.xlsx",
            date_dir / f"combined_slate_tickets_{d}_GRADED.xlsx",
            date_dir / f"combined_slate_tickets_{d}_to_grade_tomorrow_GRADED.xlsx",
        ]
        old_xlsx = next((p for p in old_candidates if p.exists()), None)
        if old_xlsx is not None:
            old_df = _read_ticket_results(old_xlsx, "old", d)
            if not old_df.empty:
                rows.append(old_df)

        # New arm: constrained replay generation (JSON-first for speed).
        regen_json = date_dir / f"combined_slate_tickets_{d}_REGEN_FAST.json"
        gen_cmd = [
            sys.executable,
            str(SCRIPTS / "combined_slate_tickets.py"),
            "--date",
            d,
            "--write-web",
            "--web-outdir",
            str(date_dir),
            "--merge-web-latest",
            "--allow-cross-date-fallback",
            "--no-ticket-model-rerank",
            "--tiers",
            "A",
            "--pick-types",
            "Standard",
            "--max-ticket-legs",
            "3",
            "--prioritize-ticket-hit",
            "--max-tickets",
            "3",
            "--ticket-gen-starts",
            "1",
            "--nba-structured-variants",
            "1",
        ]
        for key, stem in (
            ("nba", f"step8_nba_direction_clean_{d}"),
            ("nhl", f"step8_nhl_direction_clean_{d}"),
            ("soccer", f"step8_soccer_direction_clean_{d}"),
            ("tennis", f"step8_tennis_direction_clean_{d}"),
            ("mlb", f"step8_mlb_direction_clean_{d}"),
            ("nba1q", f"step8_nba1q_direction_clean_{d}"),
            ("nba1h", f"step8_nba1h_direction_clean_{d}"),
        ):
            p = _resolve_board(date_dir, stem)
            if p:
                gen_cmd.extend([f"--{key}", str(p)])

        print(f"[new] generate {d}")
        _run(gen_cmd)
        latest_json = date_dir / "tickets_latest.json"
        if latest_json.exists():
            latest_json.replace(regen_json)
        elif not regen_json.exists():
            print(f"[skip] {d} new arm missing json output")
            continue

        # Grade regenerated workbook.
        grade_cmd = [
            sys.executable,
            str(SCRIPTS / "combined_ticket_grader.py"),
            "--tickets",
            str(regen_json),
            "--nba_actuals",
            str(nba_actuals),
            "--no-ml",
        ]
        opt = _optional_actuals(date_dir, d)
        if opt.get("nba1h"):
            grade_cmd.extend(["--nba1h_actuals", str(opt["nba1h"])])
        if opt.get("nba1q"):
            grade_cmd.extend(["--nba1q_actuals", str(opt["nba1q"])])
        if opt.get("cbb"):
            grade_cmd.extend(["--cbb_actuals", str(opt["cbb"])])
        if opt.get("nhl"):
            grade_cmd.extend(["--nhl_actuals", str(opt["nhl"])])
        if opt.get("soccer"):
            grade_cmd.extend(["--soccer_actuals", str(opt["soccer"])])
        if opt.get("tennis"):
            grade_cmd.extend(["--tennis_actuals", str(opt["tennis"])])
        print(f"[new] grade {d}")
        _run(grade_cmd)
        new_graded = regen_json.with_name(regen_json.stem + "_GRADED.xlsx")
        new_df = _read_ticket_results(new_graded, "new", d)
        if not new_df.empty:
            rows.append(new_df)

    if not rows:
        raise SystemExit("No rows collected.")
    all_df = pd.concat(rows, ignore_index=True)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(args.out_csv, index=False)

    sub = all_df[all_df["mode"].isin(["power", "flex"])].copy()
    by = (
        sub.groupby(["grade_date", "arm", "mode"], dropna=False)
        .agg(
            tickets=("profit", "count"),
            cash_rate=("is_cash", "mean"),
            avg_profit=("profit", "mean"),
            total_profit=("profit", "sum"),
        )
        .reset_index()
    )
    print("\n--- A/B per-day per-mode ---")
    with pd.option_context("display.max_rows", 1000, "display.width", 160):
        print(by.to_string(index=False))

    overall = (
        sub.groupby(["arm", "mode"], dropna=False)
        .agg(
            tickets=("profit", "count"),
            cash_rate=("is_cash", "mean"),
            avg_profit=("profit", "mean"),
            total_profit=("profit", "sum"),
        )
        .reset_index()
    )
    print("\n--- A/B overall ---")
    with pd.option_context("display.max_rows", 100, "display.width", 160):
        print(overall.to_string(index=False))
    print(f"\nWrote raw rows -> {args.out_csv}")


if __name__ == "__main__":
    main()
