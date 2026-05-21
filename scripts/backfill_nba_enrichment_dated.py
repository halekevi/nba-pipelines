#!/usr/bin/env python3
"""Backfill usage_pct / team_pace onto dated NBA step8 slates for retrain joins.

When ``outputs/<date>/step8_nba_direction_clean_<date>.xlsx`` is missing but
``ui_runner/templates/graded_props_<date>.json`` exists, synthesize a minimal step8
sheet (rank_score from graded edge) and run step4b so retrain joins get usage_pct.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
STEP4B = REPO / "Sports" / "NBA" / "scripts" / "step4b_attach_nba_context.py"
NBADIR = REPO / "Sports" / "NBA"


def _rename_for_step4b(df: pd.DataFrame) -> pd.DataFrame:
    ren = {
        "Player": "player",
        "Team": "team",
        "Opp": "opp_team",
        "Pos": "pos",
    }
    return df.rename(columns={k: v for k, v in ren.items() if k in df.columns})


def synthesize_step8_from_graded(date: str) -> Path | None:
    """Build minimal step8 xlsx from graded_props JSON when pipeline snapshot is absent."""
    gp = REPO / "ui_runner" / "templates" / f"graded_props_{date}.json"
    if not gp.is_file():
        return None
    try:
        raw = json.loads(gp.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  graded_props read failed: {exc}")
        return None
    props = raw.get("props") or []
    rows: list[dict] = []
    for p in props:
        if not isinstance(p, dict) or str(p.get("sport", "")).upper() != "NBA":
            continue
        if str(p.get("result", "")).strip().upper() not in ("HIT", "MISS"):
            continue
        edge = p.get("edge")
        try:
            rank = float(edge) if edge is not None and str(edge).strip() != "" else 0.0
        except (TypeError, ValueError):
            rank = 0.0
        mm, dd = date[5:7], date[8:10]
        rows.append(
            {
                "Player": str(p.get("player", "")).strip(),
                "Team": str(p.get("team", "")).strip(),
                "Opp": str(p.get("opp_team", p.get("opp", ""))).strip(),
                "Prop": str(p.get("prop", "")).strip(),
                "Line": p.get("line"),
                "Direction": str(p.get("direction", "")).strip().upper(),
                "Pick Type": str(p.get("pick_type", "—")).strip(),
                "Rank Score": rank,
                "Edge": rank,
                "Tier": str(p.get("tier", "")).strip(),
                "Def Tier": str(p.get("def_tier", "")).strip(),
                "Game Time": f"{mm}/{dd} 7:00 PM",
            }
        )
    if not rows:
        print(f"  no decided NBA rows in {gp.name}")
        return None
    out_dir = REPO / "outputs" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx = out_dir / f"step8_nba_direction_clean_{date}.xlsx"
    pd.DataFrame(rows).to_excel(xlsx, index=False)
    print(f"  synthesized step8 from graded_props: {len(rows):,} rows -> {xlsx.name}")
    return xlsx


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
            cwd=str(NBADIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            print(r.stdout)
            print(r.stderr, file=sys.stderr)
            return False
        enriched = pd.read_csv(out, low_memory=False, encoding="utf-8-sig")
    enrich_cols = [
        "usage_pct",
        "usage_tier",
        "usage_role_type",
        "reb_pct",
        "ast_pct",
        "team_pace",
        "opp_pace",
        "game_pace",
        "pace_delta",
        "pace_context",
        "opp_def_rating",
        "opp_pts_allowed_vs_position",
        "opp_reb_allowed_vs_position",
        "opp_ast_allowed_vs_position",
        "positional_matchup_tier",
        "minutes_floor_L10",
        "minutes_ceil_L10",
        "minutes_cv_L10",
        "role_stability_score",
        "high_variance_role",
        "nba_context_source",
    ]
    for c in enrich_cols:
        if c in enriched.columns:
            df[c] = enriched[c].values
    df.to_excel(xlsx, index=False)
    up = df["usage_pct"].notna().mean() if "usage_pct" in df.columns else 0.0
    print(f"  enriched {xlsx.name}: usage_pct fill {up:.1%}")
    return True


def run_step4_pipeline(date: str, season: str) -> bool:
    nba = REPO / "outputs" / date / "nba"
    s4 = nba / "step4_with_stats.csv"
    if not s4.is_file():
        return False
    steps = [
        (
            [sys.executable, str(STEP4B), "--input", str(s4), "--output", str(s4), "--season", season],
            NBADIR,
        ),
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step5_add_line_hit_rates.py"),
                "--input",
                str(s4),
                "--output",
                str(nba / "step5_with_hit_rates.csv"),
                "--compute10",
            ],
            NBADIR,
        ),
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step6_team_role_context.py"),
                "--input",
                str(nba / "step5_with_hit_rates.csv"),
                "--output",
                str(nba / "step6_with_team_role_context.csv"),
            ],
            NBADIR,
        ),
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step6a_attach_opponent_stats_NBA.py"),
                "--input",
                str(nba / "step6_with_team_role_context.csv"),
                "--output",
                str(nba / "step6a_with_opp_stats.csv"),
            ],
            NBADIR,
        ),
    ]
    s6a = nba / "step6a_with_opp_stats.csv"
    s6c = nba / "step6c_with_schedule_flags.csv"
    gc = REPO / "Sports" / "NBA" / f"game_context_cache_{date}.csv"
    sc = REPO / "Sports" / "NBA" / f"schedule_cache_{date}.csv"
    if gc.is_file():
        steps.append(
            (
                [
                    sys.executable,
                    str(NBADIR / "scripts" / "step6b_attach_game_context.py"),
                    "--input",
                    str(s6a),
                    "--output",
                    str(nba / "step6b_with_game_context.csv"),
                    "--date",
                    date,
                    "--cache",
                    gc.name,
                ],
                NBADIR,
            )
        )
    else:
        import shutil

        shutil.copy(s6a, nba / "step6b_with_game_context.csv")
    s6b = nba / "step6b_with_game_context.csv"
    if sc.is_file():
        steps.append(
            (
                [
                    sys.executable,
                    str(NBADIR / "scripts" / "step6c_schedule_flags.py"),
                    "--input",
                    str(s6b),
                    "--output",
                    str(s6c),
                    "--date",
                    date,
                    "--cache",
                    sc.name,
                ],
                NBADIR,
            )
        )
    else:
        import shutil

        shutil.copy(s6b, s6c)
    steps.append(
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step6d_attach_h2h_matchups.py"),
                "--input",
                str(s6c),
                "--output",
                str(nba / "step6d_with_h2h.csv"),
            ],
            NBADIR,
        )
    )
    steps.append(
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step6e_attach_intel.py"),
                "--input",
                str(nba / "step6d_with_h2h.csv"),
                "--output",
                str(nba / "step6e_with_intel.csv"),
            ],
            NBADIR,
        )
    )
    s7 = nba / "step7_ranked_props.xlsx"
    steps.append(
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step7_rank_props.py"),
                "--input",
                str(nba / "step6e_with_intel.csv"),
                "--output",
                str(s7),
            ],
            NBADIR,
        )
    )
    s8csv = nba / "step8_all_direction.csv"
    steps.append(
        (
            [
                sys.executable,
                str(NBADIR / "scripts" / "step8_add_direction_context.py"),
                "--input",
                str(s7),
                "--sheet",
                "ALL",
                "--output",
                str(s8csv),
                "--date",
                date,
            ],
            NBADIR,
        )
    )
    for cmd, cwd in steps:
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            print(r.stdout, r.stderr, file=sys.stderr)
            return False
    xlsx = REPO / "outputs" / date / f"step8_nba_direction_clean_{date}.xlsx"
    if s8csv.is_file():
        pd.read_csv(s8csv, low_memory=False).to_excel(xlsx, index=False)
    return xlsx.is_file()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_date", default="2026-04-15")
    ap.add_argument("--to", dest="to_date", default="2026-05-06")
    ap.add_argument("--season", default="2025-26")
    args = ap.parse_args()
    dates = pd.date_range(args.from_date, args.to_date, freq="D")
    ok = skip = fail = 0
    for ts in dates:
        d = ts.strftime("%Y-%m-%d")
        print(f"\n=== {d} ===")
        s4 = REPO / "outputs" / d / "nba" / "step4_with_stats.csv"
        xlsx = REPO / "outputs" / d / f"step8_nba_direction_clean_{d}.xlsx"
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
            syn = synthesize_step8_from_graded(d)
            if syn is not None and enrich_step8_xlsx(syn, args.season):
                ok += 1
            else:
                print(f"  skipped (no step4, step8, or graded_props)")
                skip += 1
    print(f"\nDone: ok={ok} skip={skip} fail={fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
