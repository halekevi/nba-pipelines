#!/usr/bin/env python3
"""
CFB step8 — display workbook + hit-tracking columns for combined slate / grader.

Reads step6_ranked_cfb.xlsx (ALL), writes step8_cfb_direction_clean.xlsx.

Run:
  py -3.14 Sports/CFB/scripts/pipeline/step8_add_direction_context_cfb.py \\
      --input outputs/step6_ranked_cfb.xlsx --date 2025-11-15
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from utils.hit_tracking_columns import attach_hit_tracking_columns  # noqa: E402


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([""] * len(df), index=df.index)


def _copy_dated(out_xlsx: Path, slate_date: str) -> None:
    d = (slate_date or "").strip()
    if not d or not out_xlsx.is_file():
        return
    dated = _REPO / "outputs" / d / "cfb" / f"step8_cfb_direction_clean_{d}.xlsx"
    try:
        dated.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_xlsx, dated)
        print(f"[CFB step8] Dated copy -> {dated}")
    except Exception as exc:
        print(f"[CFB step8] WARN dated copy: {exc}")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step6_ranked_cfb.xlsx")
    ap.add_argument("--sheet", default="ALL")
    ap.add_argument("--output", default="outputs/step8_cfb_direction_clean.xlsx")
    ap.add_argument("--date", default="", help="Slate YYYY-MM-DD")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    if not inp.is_file():
        raise SystemExit(f"Missing input: {inp}")

    df = pd.read_excel(inp, sheet_name=args.sheet, engine="openpyxl")
    if df.empty:
        out = Path(args.output)
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame().to_excel(out, sheet_name="ALL", index=False)
        print(f"[CFB step8] Wrote empty {out}")
        return

    work = df.copy()
    if "player" not in work.columns:
        for c in ("player_name", "pp_player", "player_norm"):
            if c in work.columns:
                work["player"] = work[c]
                break
    if "prop_type" not in work.columns:
        work["prop_type"] = _col(work, "prop_norm", "stat_type")
    if "bet_direction" not in work.columns:
        work["bet_direction"] = _col(work, "final_bet_direction", "recommended_side", "direction")

    work = attach_hit_tracking_columns(work, "CFB")

    tier = _col(work, "tier").astype(str).str.upper().str.strip()
    rs = pd.to_numeric(_col(work, "final_score", "rank_score"), errors="coerce")
    line = pd.to_numeric(_col(work, "line", "line_score"), errors="coerce")
    proj = pd.to_numeric(_col(work, "projection"), errors="coerce")
    edge = pd.to_numeric(_col(work, "edge"), errors="coerce")
    direction = _col(work, "final_bet_direction", "bet_direction").astype(str).str.upper().str.strip()

    clean = pd.DataFrame(
        {
            "Tier": tier,
            "Rank Score": rs.round(2),
            "Player": _col(work, "player"),
            "Pos": _col(work, "pos", "position"),
            "Team": _col(work, "team", "team_abbr"),
            "Opp": _col(work, "opp_team", "opp_team_abbr", "opp"),
            "Game Time": _col(work, "start_time", "game_time"),
            "Prop": _col(work, "prop_type", "prop_norm"),
            "Pick Type": _col(work, "pick_type").fillna("Standard"),
            "Line": line,
            "Direction": direction,
            "Edge": edge.round(2),
            "Projection": proj.round(2),
            "Hit Rate": pd.to_numeric(work.get("hit_rate"), errors="coerce"),
            "Hit Rate L5": pd.to_numeric(work.get("hit_rate_l5"), errors="coerce"),
            "Hit Rate L10": pd.to_numeric(work.get("hit_rate_l10"), errors="coerce"),
            "L5 Over": pd.to_numeric(work.get("l5_over"), errors="coerce"),
            "L5 Under": pd.to_numeric(work.get("l5_under"), errors="coerce"),
            "L10 Over": pd.to_numeric(work.get("l10_over"), errors="coerce"),
            "L10 Under": pd.to_numeric(work.get("l10_under"), errors="coerce"),
            "Strat Hit Rate": pd.to_numeric(work.get("strat_hit_rate"), errors="coerce"),
            "Strat N": pd.to_numeric(work.get("strat_n"), errors="coerce"),
            "Sport Maturity": work.get("sport_signal_maturity", ""),
            "Confidence Tier": work.get("confidence_tier", ""),
            "Confidence Score": pd.to_numeric(work.get("confidence_score"), errors="coerce"),
            "Confidence Note": work.get("confidence_note", ""),
            "Def Tier": _col(work, "def_tier", "opp_def_tier"),
        }
    )

    pt_low = clean["Pick Type"].astype(str).str.strip().str.lower()
    forced = pt_low.isin(("goblin", "demon"))
    ln = pd.to_numeric(clean["Line"], errors="coerce")
    pj = pd.to_numeric(clean["Projection"], errors="coerce")
    has_pl = ln.notna() & pj.notna()
    signed_gap = pj - ln
    clean["Edge"] = signed_gap.where(has_pl, pd.to_numeric(clean["Edge"], errors="coerce")).round(2)
    clean["Abs Edge"] = pd.to_numeric(clean["Edge"], errors="coerce").abs().round(2)
    e_num = pd.to_numeric(clean["Edge"], errors="coerce")
    from_side = np.where(e_num >= 0, "OVER", "UNDER")
    d_prev = clean["Direction"].astype(str).str.upper().str.strip().replace("", "OVER")
    clean["Direction"] = np.where(
        forced.to_numpy(),
        "OVER",
        np.where(has_pl.to_numpy(), from_side, d_prev.to_numpy()),
    )

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        clean.to_excel(w, sheet_name="ALL", index=False)
        for t in ("A", "B", "C", "D"):
            sub = clean[clean["Tier"] == t]
            if len(sub):
                sub.to_excel(w, sheet_name=f"Tier {t}", index=False)
    print(f"[CFB step8] Wrote {out_path} rows={len(clean)}")
    _copy_dated(out_path, str(args.date or "").strip())


if __name__ == "__main__":
    main()
