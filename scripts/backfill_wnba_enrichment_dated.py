#!/usr/bin/env python3
"""Backfill WNBA step4b enrichment onto dated step8 slates for retrain joins."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
STEP4B = REPO / "Sports" / "WNBA" / "scripts" / "step4b_attach_wnba_context.py"
WNBADIR = REPO / "Sports" / "WNBA"


def _rename_for_step4b(df: pd.DataFrame) -> pd.DataFrame:
    ren = {
        "Player": "player",
        "Team": "team",
        "Opp": "opp_team",
        "Pos": "pos",
        "Game Date": "game_date",
    }
    return df.rename(columns={k: v for k, v in ren.items() if k in df.columns})


def enrich_step8_xlsx(xlsx: Path, season: str) -> bool:
    df = pd.read_excel(xlsx, engine="openpyxl")
    if "usage_pct" in df.columns and df["usage_pct"].notna().mean() > 0.5:
        print(f"  skip (already enriched): {xlsx.name}")
        return True
    work = _rename_for_step4b(df)
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.csv"
        out = Path(td) / "out.csv"
        work.to_csv(inp, index=False, encoding="utf-8-sig")
        r = subprocess.run(
            [
                sys.executable,
                str(STEP4B),
                "--input",
                str(inp),
                "--output",
                str(out),
                "--season",
                season,
            ],
            cwd=str(WNBADIR),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(r.stdout)
            print(r.stderr, file=sys.stderr)
            return False
        enriched = pd.read_csv(out, low_memory=False, encoding="utf-8-sig")
    enrich_cols = [
        "usage_pct",
        "usage_tier",
        "min_per_game",
        "team_pace",
        "opp_pace",
        "pace_delta",
        "pace_context",
        "star_tier",
        "is_franchise_star",
        "foul_rate_per_36",
        "foul_trouble_risk",
        "b2b_flag",
        "wnba_b2b_weight",
        "b2b_rest_context",
        "hhs_efg_pct",
        "hhs_ts_pct",
        "hhs_per",
        "wnba_context_source",
    ]
    for c in enrich_cols:
        if c in enriched.columns:
            df[c] = enriched[c].values
    df.to_excel(xlsx, index=False)
    up = df["usage_pct"].notna().mean() if "usage_pct" in df.columns else 0.0
    print(f"  enriched {xlsx.name}: usage_pct fill {up:.1%}")
    return True


def run_step4_pipeline(date: str, season: str) -> bool:
    wnba = REPO / "outputs" / date / "wnba"
    s4 = wnba / "step4_wnba_stats.csv"
    if not s4.is_file():
        return False
    steps: list[tuple[list[str], Path]] = [
        (
            [sys.executable, str(STEP4B), "--input", str(s4), "--output", str(s4), "--season", season],
            WNBADIR,
        ),
        (
            [
                sys.executable,
                str(WNBADIR / "step5_add_line_hit_rates.py"),
                "--input",
                str(s4),
                "--output",
                str(wnba / "step5_wnba_hitrates.csv"),
                "--compute10",
            ],
            WNBADIR,
        ),
        (
            [
                sys.executable,
                str(WNBADIR / "step6_team_role_context.py"),
                "--input",
                str(wnba / "step5_wnba_hitrates.csv"),
                "--output",
                str(wnba / "step6_wnba_context.csv"),
            ],
            WNBADIR,
        ),
        (
            [
                sys.executable,
                str(WNBADIR / "step7_rank_props.py"),
                "--input",
                str(wnba / "step6_wnba_context.csv"),
                "--output",
                str(wnba / "step7_wnba_ranked.xlsx"),
            ],
            WNBADIR,
        ),
        (
            [
                sys.executable,
                str(WNBADIR / "step8_add_direction_context.py"),
                "--input",
                str(wnba / "step7_wnba_ranked.xlsx"),
                "--sheet",
                "ALL",
                "--output",
                str(wnba / "step8_wnba_direction.csv"),
                "--xlsx",
                str(wnba / "step8_wnba_direction_clean.xlsx"),
                "--date",
                date,
            ],
            WNBADIR,
        ),
    ]
    for cmd, cwd in steps:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout, r.stderr, file=sys.stderr)
            return False
    dated = REPO / "outputs" / date / f"step8_wnba_direction_clean_{date}.xlsx"
    src = wnba / "step8_wnba_direction_clean.xlsx"
    if src.is_file():
        import shutil

        shutil.copy(src, dated)
    return dated.is_file()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_date", default="2026-04-15")
    ap.add_argument("--to", dest="to_date", default="2026-05-20")
    ap.add_argument("--season", default="2025")
    args = ap.parse_args()
    dates = pd.date_range(args.from_date, args.to_date, freq="D")
    ok = skip = fail = 0
    for ts in dates:
        d = ts.strftime("%Y-%m-%d")
        print(f"\n=== {d} ===")
        s4 = REPO / "outputs" / d / "wnba" / "step4_wnba_stats.csv"
        xlsx = REPO / "outputs" / d / f"step8_wnba_direction_clean_{d}.xlsx"
        if s4.is_file():
            if run_step4_pipeline(d, args.season):
                print(f"  pipeline OK {d}")
                ok += 1
            else:
                print(f"  pipeline FAIL {d}")
                fail += 1
        elif xlsx.is_file():
            if enrich_step8_xlsx(xlsx, args.season):
                ok += 1
            else:
                fail += 1
        else:
            print(f"  skipped (no step4 or step8)")
            skip += 1
    print(f"\nDone: ok={ok} skip={skip} fail={fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
