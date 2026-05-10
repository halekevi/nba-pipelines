#!/usr/bin/env python3
"""
NFL step8 — formatted direction workbook for combined_slate_tickets / web UI.

Reads step7_nfl_ranked.xlsx (ALL), writes step8_nfl_direction_clean.xlsx with display columns.

Run from NFL/ with NFL_PIPELINE_ACTIVE=1.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit


def _copy_dated(out_xlsx: Path, slate_date: str) -> None:
    if not out_xlsx.is_file():
        return
    d = (slate_date or "").strip() or date.today().isoformat()
    repo_root = Path(__file__).resolve().parent.parent
    for dated_dir in (repo_root / "outputs" / d, repo_root / "NFL" / "data" / "outputs" / d):
        try:
            dated_dir.mkdir(parents=True, exist_ok=True)
            dst = dated_dir / f"step8_nfl_direction_clean_{d}.xlsx"
            shutil.copy2(out_xlsx, dst)
            print(f"[NFL step8] Dated copy -> {dst}")
        except Exception as e:
            print(f"[NFL step8] WARN dated copy: {e}")


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step7_nfl_ranked.xlsx")
    ap.add_argument("--sheet", default="ALL")
    ap.add_argument("--output", default="outputs/step8_nfl_direction_clean.xlsx")
    ap.add_argument("--date", default="", help="Pipeline slate date YYYY-MM-DD (for dated copies)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.is_file():
        print(f"[NFL step8] Missing input: {src}")
        sys.exit(1)

    df = pd.read_excel(src, sheet_name=args.sheet, engine="openpyxl")
    if df.empty:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame().to_excel(out, sheet_name="ALL", index=False)
        print(f"[NFL step8] Wrote empty {out}")
        return

    def col(*names: str) -> pd.Series:
        for n in names:
            if n in df.columns:
                return df[n]
        return pd.Series([""] * len(df), index=df.index)

    player = col("player_name", "player")
    tier = col("tier").astype(str).str.upper().str.strip()
    rs = pd.to_numeric(col("rank_score", "prop_score"), errors="coerce")
    pos = col("position_group", "pos")
    team = col("team")
    opp = col("opp_team", "opponent")
    gt = col("start_time", "game_time")
    prop = col("stat_type", "prop_type", "prop_type_normalized")
    pt = col("pick_type")
    line = pd.to_numeric(col("line_score", "line"), errors="coerce")
    direction = col("recommended_side", "bet_direction", "direction").astype(str).str.upper().str.strip()
    edge = pd.to_numeric(col("edge"), errors="coerce")
    proj = pd.to_numeric(col("projection"), errors="coerce")
    hr = pd.to_numeric(col("hit_rate", "composite_hit_rate"), errors="coerce")
    l5o = pd.to_numeric(col("l5_over", "last5_over"), errors="coerce")
    l5u = pd.to_numeric(col("l5_under", "last5_under"), errors="coerce")
    dtr = col("def_tier")
    tm_l5_rec = col("team_last5_record")
    tm_l5_pf = pd.to_numeric(col("team_last5_pf_pg"), errors="coerce")
    tm_l5_pa = pd.to_numeric(col("team_last5_pa_pg"), errors="coerce")
    tm_l5_pm = pd.to_numeric(col("team_last5_margin_avg"), errors="coerce")
    op_l5_rec = col("opp_last5_record")
    op_l5_pf = pd.to_numeric(col("opp_last5_pf_pg"), errors="coerce")
    op_l5_pa = pd.to_numeric(col("opp_last5_pa_pg"), errors="coerce")
    op_l5_pm = pd.to_numeric(col("opp_last5_margin_avg"), errors="coerce")

    clean = pd.DataFrame(
        {
            "Tier": tier,
            "Rank Score": rs.round(2),
            "Player": player,
            "Pos": pos,
            "Team": team,
            "Opp": opp,
            "Game Time": gt,
            "Prop": prop,
            "Pick Type": pt.fillna("Standard"),
            "Line": line,
            "Direction": direction,
            "Edge": edge.round(2),
            "Projection": proj.round(2),
            "Hit Rate (5g)": hr,
            "L5 Over": l5o,
            "L5 Under": l5u,
            "Team L5": tm_l5_rec,
            "Tm L5 PF/G": tm_l5_pf.round(1),
            "Tm L5 PA/G": tm_l5_pa.round(1),
            "Tm L5 +/-": tm_l5_pm.round(1),
            "Opp L5": op_l5_rec,
            "Opp L5 PF/G": op_l5_pf.round(1),
            "Opp L5 PA/G": op_l5_pa.round(1),
            "Opp L5 +/-": op_l5_pm.round(1),
            "Def Tier": dtr,
        }
    )

    pt_low = clean["Pick Type"].astype(str).str.strip().str.lower()
    forced_rows = pt_low.isin(("goblin", "demon"))
    ln = pd.to_numeric(clean["Line"], errors="coerce")
    pj = pd.to_numeric(clean["Projection"], errors="coerce")
    has_pl = ln.notna() & pj.notna()
    prev_edge = pd.to_numeric(clean["Edge"], errors="coerce")
    signed_gap = pj - ln
    clean["Edge"] = signed_gap.where(has_pl, prev_edge).round(2)
    clean["Abs Edge"] = pd.to_numeric(clean["Edge"], errors="coerce").abs().round(2)

    d_prev = clean["Direction"].astype(str).str.upper().str.strip().replace("", "OVER")
    e_num = pd.to_numeric(clean["Edge"], errors="coerce")
    from_side = np.where(e_num >= 0, "OVER", "UNDER")
    clean["Direction"] = np.where(
        forced_rows.to_numpy(),
        "OVER",
        np.where(has_pl.to_numpy(), from_side, d_prev.to_numpy()),
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        clean.to_excel(w, sheet_name="ALL", index=False)
        for t in ("A", "B", "C", "D"):
            sub = clean[clean["Tier"] == t]
            if len(sub):
                sub.to_excel(w, sheet_name=f"Tier {t}", index=False)
    print(f"[NFL step8] Wrote {out_path} rows={len(clean)}")
    _copy_dated(out_path, str(args.date or "").strip())


if __name__ == "__main__":
    main()
