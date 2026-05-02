#!/usr/bin/env python3
"""
grade_cbb_full_slate.py  (v2 — with defensive tier grading)

Grades a FULL CBB slate using:
- Slate file: step3b or step5b CSV (must have line, prop_norm, model_dir_5 or final_bet_direction,
  espn_athlete_id, and optionally opp_def_tier / OVERALL_DEF_RANK from step3b)
- Actuals file: cbb_actuals_YYYY-MM-DD.csv (from fetch_cbb_actuals_by_date.py)

New vs original:
- Carries opp_def_tier, opp_def_rank through to graded output
- Prints full summary table: hits/misses by Defensive Tier x Pick Type x Tier (A/B/C/D)
- Writes multi-sheet Excel: Summary + By Def Tier + Box Raw

One-line example:
  py -3.14 grade_cbb_full_slate.py \\
      --slate step5b_with_stats_cbb.csv \\
      --actuals cbb_actuals_2026-02-20.csv \\
      --out cbb_graded_2026-02-20.xlsx
"""

from __future__ import annotations

import argparse
from typing import Optional

import pandas as pd
import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────────

def to_float(x) -> float:
    try:
        s = str(x).strip()
        if s in ("", "none", "nan", "--"):
            return np.nan
        return float(s)
    except Exception:
        return np.nan


def stat_from_row(actuals_row: pd.Series, prop_norm: str) -> float:
    p = (prop_norm or "").strip().lower()

    pts = to_float(actuals_row.get("PTS"))
    reb = to_float(actuals_row.get("REB"))
    ast = to_float(actuals_row.get("AST"))
    stl = to_float(actuals_row.get("STL"))
    blk = to_float(actuals_row.get("BLK"))
    tov = to_float(actuals_row.get("TO"))
    pm3 = to_float(actuals_row.get("3PT") or actuals_row.get("3PM"))

    if p in ("pts", "points"):             return pts
    if p in ("reb", "rebs", "rebounds"):   return reb
    if p in ("ast", "assists"):            return ast
    if p in ("stl", "steals"):             return stl
    if p in ("blk", "blocks"):             return blk
    if p in ("tov", "to", "turnovers"):    return tov
    if p in ("3pm", "3-pt made"):          return pm3
    if p in ("pr", "pts+rebs", "pts+reb"): return pts + reb
    if p in ("pa", "pts+asts", "pts+ast"): return pts + ast
    if p in ("ra", "rebs+asts", "reb+ast"):return reb + ast
    if p in ("pra", "pts+rebs+asts"):      return pts + reb + ast
    if p in ("stocks", "stl+blk"):         return stl + blk
    if "fantasy" in p:
        return pts + 1.2*reb + 1.5*ast + 3*stl + 3*blk - tov

    return np.nan


def grade_row(actual_value: float, line: float, dir_played: str) -> str:
    if np.isnan(actual_value) or np.isnan(line) or not dir_played:
        return "VOID"
    d = dir_played.strip().upper()
    if abs(actual_value - line) < 1e-9:
        return "PUSH"
    if d == "OVER":
        return "HIT" if actual_value > line else "MISS"
    if d == "UNDER":
        return "HIT" if actual_value < line else "MISS"
    return "VOID"


# ── Summary builder ───────────────────────────────────────────────────────────

def build_summary_block(df: pd.DataFrame, label_col: str, label_vals: list,
                         title: str) -> pd.DataFrame:
    """Build a hits/misses/hit-rate table for a given grouping column."""
    decided = df[df["result"].isin(["HIT", "MISS"])]
    rows = []
    for val in label_vals:
        sub = decided[decided[label_col].astype(str) == str(val)]
        hits   = (sub["result"] == "HIT").sum()
        misses = (sub["result"] == "MISS").sum()
        voids  = (df[df[label_col].astype(str) == str(val)]["result"]
                  .isin(["VOID", "PUSH"]).sum())
        total  = hits + misses
        hr     = hits / total if total > 0 else np.nan
        rows.append({
            "Group": title,
            "Label": val,
            "Hits":  hits,
            "Misses":misses,
            "Voids": voids,
            "Decided": total,
            "Hit Rate": round(hr, 4) if not np.isnan(hr) else "",
        })
    return pd.DataFrame(rows)


def build_crosstab(df: pd.DataFrame, row_col: str, col_col: str,
                   row_vals: list, col_vals: list) -> pd.DataFrame:
    """
    Build a crosstab of hit rate: row_col x col_col.
    Returns a DataFrame of shape (len(row_vals), len(col_vals)*3).
    """
    decided = df[df["result"].isin(["HIT", "MISS"])]
    records = []
    for rv in row_vals:
        row_data = {"": rv}
        for cv in col_vals:
            sub = decided[(decided[row_col].astype(str) == str(rv)) &
                          (decided[col_col].astype(str) == str(cv))]
            hits   = (sub["result"] == "HIT").sum()
            misses = (sub["result"] == "MISS").sum()
            total  = hits + misses
            hr     = f"{hits/total:.1%}" if total > 0 else "—"
            row_data[f"{cv} H/M"]  = f"{hits}/{misses}"
            row_data[f"{cv} HR"]   = hr
        records.append(row_data)
    return pd.DataFrame(records)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate",   required=True, help="Slate CSV (step5b or step3b output)")
    ap.add_argument("--actuals", required=True, help="Actuals CSV from ESPN fetcher")
    ap.add_argument("--out",     required=True, help="Output .xlsx")
    args = ap.parse_args()

    # ── Load ────────────────────────────────────────────────────────────────
    slate   = pd.read_csv(args.slate,   dtype=str).fillna("")
    actuals = pd.read_csv(args.actuals, dtype=str).fillna("")

    for req in ("prop_norm", "line"):
        if req not in slate.columns:
            raise RuntimeError(f"Slate missing required column: {req}")

    # espn_athlete_id is preferred but not required — fall back to player_norm name matching
    if "espn_athlete_id" not in slate.columns:
        print("  ⚠️  No 'espn_athlete_id' in slate — using name-only matching.")
        slate["espn_athlete_id"] = ""
    if "player_norm" not in slate.columns:
        if "player" in slate.columns:
            import re, unicodedata
            def _norm(s):
                s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode("ascii")
                s = s.lower().strip()
                s = re.sub(r"[^a-z0-9 ]+", " ", s)
                return re.sub(r"\s+", " ", s).strip()
            slate["player_norm"] = slate["player"].apply(_norm)
        else:
            raise RuntimeError("Slate missing both 'player' and 'player_norm' columns.")

    # Resolve direction column (step5b uses model_dir_5, step6 uses final_bet_direction)
    dir_col = next((c for c in ("final_bet_direction", "model_dir_5", "bet_direction",
                                "model_dir", "model_direction")
                    if c in slate.columns), None)
    if dir_col is None:
        slate["_dir"] = ""
    else:
        slate["_dir"] = slate[dir_col].astype(str).str.upper().str.strip()

    # Resolve defensive tier columns (from step3b)
    def_tier_col = next((c for c in ("opp_def_tier", "def_tier", "Def Tier") if c in slate.columns), None)
    def_rank_col = next((c for c in ("opp_def_rank", "OVERALL_DEF_RANK", "opp_def_adj_de")
                         if c in slate.columns), None)

    # Pick type / tier cols
    pick_type_col = next((c for c in ("pick_type", "Pick Type") if c in slate.columns), None)
    tier_col      = next((c for c in ("tier", "Tier") if c in slate.columns), None)

    # ── Build actuals index (max minutes if duped) ──────────────────────────
    actuals["_min"] = actuals["MIN"].apply(to_float)
    actuals = (actuals.sort_values("_min", ascending=False)
                      .drop_duplicates(subset=["espn_athlete_id"], keep="first")
                      .drop(columns=["_min"]))
    actuals_idx = actuals.set_index("espn_athlete_id", drop=False)

    # Secondary: name-based index for rows without espn_athlete_id
    import re as _re, unicodedata as _uc
    def _norm(s):
        s = _uc.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode("ascii")
        s = s.lower().strip()
        s = _re.sub(r"[^a-z0-9 ]+", " ", s)
        return _re.sub(r"\s+", " ", s).strip()
    actuals_name_idx = actuals.copy()
    actuals_name_idx["_name_norm"] = actuals_name_idx["player_name"].apply(_norm)
    actuals_name_idx = actuals_name_idx.drop_duplicates(subset=["_name_norm"], keep="first")
    actuals_name_idx = actuals_name_idx.set_index("_name_norm", drop=False)

    # ── Grade each row ──────────────────────────────────────────────────────
    actual_values, actual_statuses = [], []

    for _, r in slate.iterrows():
        aid = str(r.get("espn_athlete_id", "")).strip()
        pnorm = str(r.get("player_norm", "")).strip()
        arow = None
        if aid and aid in actuals_idx.index:
            arow = actuals_idx.loc[aid]
            method = "ID"
        elif pnorm and pnorm in actuals_name_idx.index:
            arow = actuals_name_idx.loc[pnorm]
            method = "NAME"
        else:
            method = "MISSING"

        if arow is None:
            actual_values.append(np.nan); actual_statuses.append("NO_ACTUAL_FOUND"); continue
        av = stat_from_row(arow, str(r.get("prop_norm", "")))
        actual_values.append(av)
        actual_statuses.append("OK" if not np.isnan(av) else "UNSUPPORTED_PROP")

    out = slate.copy()
    out["line_num"]     = out["line"].apply(to_float)
    out["actual_value"] = actual_values
    out["dir_played"]   = out["_dir"]
    out["result"]       = [grade_row(av, ln, d)
                           for av, ln, d in zip(out["actual_value"], out["line_num"], out["dir_played"])]
    out["actual_status"] = actual_statuses
    out["diff"]          = out["actual_value"] - out["line_num"]
    out.drop(columns=["_dir"], inplace=True, errors="ignore")

    # ── Console summary ─────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f" CBB GRADED SUMMARY")
    print(f"{'='*55}")
    print(f" Total rows   : {len(out)}")
    print(f" Actual OK    : {(out['actual_status']=='OK').sum()}")
    print(f" HIT          : {(out['result']=='HIT').sum()}")
    print(f" MISS         : {(out['result']=='MISS').sum()}")
    print(f" PUSH         : {(out['result']=='PUSH').sum()}")
    print(f" VOID         : {(out['result']=='VOID').sum()}")

    decided = out[out["result"].isin(["HIT", "MISS"])]
    if len(decided) > 0:
        overall_hr = (decided["result"] == "HIT").sum() / len(decided)
        print(f" Hit Rate     : {overall_hr:.1%} ({(decided['result']=='HIT').sum()}/{len(decided)} decided)")

    # Defensive tier breakdown
    if def_tier_col:
        print(f"\n BY DEFENSIVE TIER  (col: {def_tier_col})")
        print(f" {'Tier':<12} {'Hits':>5} {'Misses':>7} {'Decided':>8} {'Hit Rate':>10}")
        print(f" {'-'*46}")
        for tier in ["Elite", "Above Avg", "Avg", "Weak", ""]:
            sub_d = decided[decided[def_tier_col].astype(str) == str(tier)]
            if len(sub_d) == 0:
                continue
            h = (sub_d["result"] == "HIT").sum()
            m = (sub_d["result"] == "MISS").sum()
            label = tier if tier else "UNMAPPED"
            print(f" {label:<12} {h:>5} {m:>7} {h+m:>8} {h/(h+m):>9.1%}")
    else:
        print("\n ⚠️  No defensive tier column found in slate.")
        print("    Run cbb_step3b_attach_def_rankings.py first to enable this breakdown.")

    print(f"{'='*55}\n")

    # ── Build Excel sheets ──────────────────────────────────────────────────
    summary_blocks = []

    # Overall
    overall_rows = []
    for result_val in ["HIT", "MISS", "PUSH", "VOID"]:
        overall_rows.append({"Group": "OVERALL", "Label": result_val,
                             "Count": (out["result"] == result_val).sum()})
    overall_df = pd.DataFrame(overall_rows)

    # By Def Tier
    if def_tier_col:
        tier_order = ["Elite", "Above Avg", "Avg", "Weak"]
        tier_block = build_summary_block(out, def_tier_col, tier_order, "Def Tier")
        summary_blocks.append(("By Def Tier", tier_block))

        # By Def Tier x Pick Type crosstab
        if pick_type_col:
            pick_types = ["Goblin", "Demon", "Standard"]
            xtab = build_crosstab(out, def_tier_col, pick_type_col,
                                  tier_order, pick_types)
            summary_blocks.append(("Def Tier x Pick Type", xtab))

        # By Def Tier x Model Tier (A/B/C/D)
        if tier_col:
            model_tiers = ["A", "B", "C", "D"]
            xtab2 = build_crosstab(out, def_tier_col, tier_col,
                                   tier_order, model_tiers)
            summary_blocks.append(("Def Tier x Model Tier", xtab2))

    # By Pick Type
    if pick_type_col:
        pt_block = build_summary_block(out, pick_type_col,
                                       ["Goblin", "Demon", "Standard"], "Pick Type")
        summary_blocks.append(("By Pick Type", pt_block))

    # By Model Tier
    if tier_col:
        mt_block = build_summary_block(out, tier_col, ["A","B","C","D"], "Model Tier")
        summary_blocks.append(("By Model Tier", mt_block))

    # ── Write Excel ─────────────────────────────────────────────────────────
    with pd.ExcelWriter(args.out, engine="openpyxl") as xw:
        # Summary sheet
        row_cursor = 0
        summary_combined = pd.concat([b for _, b in summary_blocks], ignore_index=True) \
                           if summary_blocks else pd.DataFrame()

        overall_df.to_excel(xw, sheet_name="Summary", index=False, startrow=0)
        if not summary_combined.empty:
            summary_combined.to_excel(xw, sheet_name="Summary", index=False,
                                      startrow=len(overall_df) + 2)

        # Individual breakdown sheets
        for sheet_name, block_df in summary_blocks:
            block_df.to_excel(xw, sheet_name=sheet_name[:31], index=False)

        # Box Raw — full graded data
        out.to_excel(xw, sheet_name="Box Raw", index=False)

        # Decided only
        decided.to_excel(xw, sheet_name="Decided Only", index=False)

    print(f"✅ Saved: {args.out}")
    print(f"   Sheets: Summary | By Def Tier | Def Tier x Pick Type | Def Tier x Model Tier")
    print(f"           By Pick Type | By Model Tier | Box Raw | Decided Only")


if __name__ == "__main__":
    main()
