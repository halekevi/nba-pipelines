#!/usr/bin/env python3
"""
step8_add_direction_context.py
-------------------------------
Reads step7_ranked_props.xlsx, appends direction context columns,
and outputs both a full CSV and a clean formatted XLSX for decision making.

Adds:
- model_dir
- final_bet_direction
- final_dir_reason

Run:
  py -3.14 step8_add_direction_context.py --input step7_ranked_props.xlsx --sheet ALL --output step8_all_direction.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import os

_ET = ZoneInfo("America/New_York")


def _start_times_et(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce").dt.tz_convert(_ET)


def _format_et_clock(et: pd.Series) -> pd.Series:
    clk = et.dt.strftime("%I:%M %p")
    return clk.str.replace(r"^0(\d:)", r"\1", regex=True)


def _copy_dated_step8_wnba(output_xlsx_path: str, slate_date: str) -> None:
    """Dated mirror of clean step8 is handled in scripts/run_wnba_pipeline.ps1 (Publish-WnbaStep8CleanArtifacts).

    Avoid writing step1–7 or extra step8 copies into repo ``outputs/<date>/`` from Python; that script
    publishes ``outputs/<date>/step8_wnba_direction_clean_<date>.xlsx`` and ``data/outputs/`` only.
    """
    _ = (output_xlsx_path, slate_date)
    return


def _norm_pick_type(x: str) -> str:
    t = (str(x) if x is not None else "").strip().lower()
    if "gob" in t:
        return "Goblin"
    if "dem" in t:
        return "Demon"
    return "Standard"

TIER_COLORS = {
    'A': ('1E8449', 'FFFFFF'),
    'B': ('2874A6', 'FFFFFF'),
    'C': ('D4AC0D', '000000'),
    'D': ('717D7E', 'FFFFFF'),
}
HEADER_COLOR = '1C1C1C'

def thin_border():
    s = Side(style='thin', color='DDDDDD')
    return Border(left=s, right=s, top=s, bottom=s)

def write_sheet(wb, name, data):
    ws = wb.create_sheet(name)
    tier_bg, tier_fg = TIER_COLORS.get(name, ('333333', 'FFFFFF'))
    headers = list(data.columns)

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = Font(bold=True, color='FFFFFF', name='Arial', size=10)
        cell.fill = PatternFill('solid', start_color=HEADER_COLOR)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = thin_border()
    ws.row_dimensions[1].height = 30

    for ri, row in enumerate(data.itertuples(index=False), 2):
        direction = row[headers.index('Direction')] if 'Direction' in headers else ''
        for ci, val in enumerate(row, 1):
            col_name = headers[ci - 1]
            display_val = '' if pd.isna(val) else val
            cell = ws.cell(row=ri, column=ci, value=display_val)
            cell.font = Font(name='Arial', size=9)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border()

            if col_name == 'Tier':
                cell.fill = PatternFill('solid', start_color=tier_bg)
                cell.font = Font(bold=True, color=tier_fg, name='Arial', size=9)
            elif col_name == 'Direction':
                bg = 'C8F7C5' if val == 'OVER' else 'F7C5C5' if val == 'UNDER' else 'FFFFFF'
                cell.fill = PatternFill('solid', start_color=bg)
                cell.font = Font(bold=True, name='Arial', size=9)
            else:
                cell.fill = PatternFill('solid', start_color='F9F9F9' if ri % 2 == 0 else 'FFFFFF')

    col_widths = {
        'Tier': 6, 'Rank Score': 10, 'Player': 18, 'Pos': 6,
        'Team': 6, 'Opp': 6, 'Game Date': 12, 'Game Time': 10,
        'Prop': 16, 'Pick Type': 10, 'Line': 7,
        'Direction': 9, 'Edge': 7, 'Projection': 10,
        'ML Prob': 9, 'Edge Score': 10, 'Blended Score': 12,
        'Hit Rate (5g)': 12, 'Last 5 Avg': 10, 'Season Avg': 10,
        'L5 Over': 8, 'L5 Under': 8,
        'Def Rank': 9, 'Def Tier': 10,
        'Min Tier': 9, 'Shot Role': 10, 'Usage Role': 10,
        'Void Reason': 20,
    }
    for ci, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(ci)].width = col_widths.get(h, 12)

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

def build_clean_xlsx(df: pd.DataFrame, xlsx_path: str):
    df2 = df.copy()
    if "start_time" in df2.columns:
        et = _start_times_et(df2["start_time"])
        parsed_gd = et.dt.strftime("%Y-%m-%d").where(et.notna(), "").astype(str).str.strip()
        prev = df2.get("game_date", pd.Series([""] * len(df2))).astype(str).str.strip().str[:10]
        prev_ok = prev.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
        df2["game_time"] = _format_et_clock(et)
        # Prefer upstream game_date when it is a valid YYYY-MM-DD (step1 may anchor it to pipeline
        # --date for full boards). Only fall back to start_time ET when game_date is missing/wrong.
        df2["game_date"] = np.where(
            prev_ok,
            prev,
            np.where(parsed_gd.str.len() > 0, parsed_gd, ""),
        )
    else:
        if "game_date" not in df2.columns:
            df2["game_date"] = ""
        if "game_time" not in df2.columns:
            df2["game_time"] = ""

    keep = [
        'tier', 'rank_score',
        'player', 'pos', 'team', 'opp_team', 'game_date', 'game_time',
        'prop_type', 'pick_type', 'line',
        'final_bet_direction',
        'edge', 'projection',
        'ml_prob',
        'edge_score',
        'blended_score',
        'line_hit_rate_over_ou_5',
        'stat_last5_avg', 'stat_season_avg',
        'last5_over', 'last5_under',
        'OVERALL_DEF_RANK', 'DEF_TIER',
        'minutes_tier', 'shot_role', 'usage_role',
        'void_reason',
    ]
    # only keep cols that exist
    keep = [c for c in keep if c in df2.columns]
    clean = df2[keep].copy()

    for col in ['rank_score', 'edge', 'projection', 'ml_prob', 'edge_score', 'blended_score', 'line_hit_rate_over_ou_5']:
        if col in clean.columns:
            rnd = 4 if col in ('ml_prob', 'edge_score', 'blended_score') else 2
            clean[col] = pd.to_numeric(clean[col], errors='coerce').round(rnd)
    for col in ['stat_last5_avg', 'stat_season_avg']:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors='coerce').round(1)
    for col in ['last5_over', 'last5_under']:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors='coerce').astype('Int64')

    tier_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    clean['_tier_sort'] = clean['tier'].map(tier_order)
    clean = clean.sort_values(['_tier_sort', 'rank_score'], ascending=[True, False]).drop(columns='_tier_sort')

    rename = {
        'tier': 'Tier', 'rank_score': 'Rank Score',
        'player': 'Player', 'pos': 'Pos', 'team': 'Team', 'opp_team': 'Opp',
        'game_date': 'Game Date', 'game_time': 'Game Time',
        'prop_type': 'Prop', 'pick_type': 'Pick Type', 'line': 'Line',
        'final_bet_direction': 'Direction',
        'edge': 'Edge', 'projection': 'Projection',
        'ml_prob':            'ML Prob',
        'edge_score':         'Edge Score',
        'blended_score':      'Blended Score',
        'line_hit_rate_over_ou_5': 'Hit Rate (5g)',
        'stat_last5_avg': 'Last 5 Avg', 'stat_season_avg': 'Season Avg',
        'last5_over': 'L5 Over', 'last5_under': 'L5 Under',
        'OVERALL_DEF_RANK': 'Def Rank', 'DEF_TIER': 'Def Tier',
        'minutes_tier': 'Min Tier', 'shot_role': 'Shot Role', 'usage_role': 'Usage Role',
        'void_reason': 'Void Reason',
    }
    clean = clean.rename(columns=rename)

    wb = Workbook()
    wb.remove(wb.active)
    write_sheet(wb, 'ALL', clean)
    for tier in ['A', 'B', 'C', 'D']:
        subset = clean[clean['Tier'] == tier].copy()
        if len(subset):
            write_sheet(wb, f'Tier {tier}', subset)

    wb.save(xlsx_path)
    print(f"📊 Clean XLSX saved → {xlsx_path}")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step7_ranked_props.xlsx")
    ap.add_argument("--sheet", default="ALL")
    ap.add_argument("--output", default="step8_all_direction.csv")
    ap.add_argument("--xlsx", default="")  # optional override for xlsx path
    ap.add_argument(
        "--date",
        default="",
        help="Slate date YYYY-MM-DD (for dated snapshot copies; same as pipeline -Date)",
    )
    args = ap.parse_args()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    print(f"→ Loading: {args.input} (sheet={args.sheet})")
    df = pd.read_excel(args.input, sheet_name=args.sheet, dtype=str).fillna("")

    out = df.copy()

    if "edge" not in out.columns:
        proj = pd.to_numeric(out.get("projection", ""), errors="coerce")
        line = pd.to_numeric(out.get("line", ""), errors="coerce")
        out["edge"] = (proj - line)

    edge = pd.to_numeric(out["edge"], errors="coerce")
    abs_edge = edge.abs()

    pick_type = out.get("pick_type", "Standard").astype(str).apply(_norm_pick_type)
    forced = pick_type.isin(["Goblin", "Demon"])

    model_dir = np.where(edge >= 0, "OVER", "UNDER")
    out["model_dir"] = model_dir

    final_dir = model_dir.copy()
    reason = np.where(abs_edge < 0.03, "MODEL_TIEBREAK_DIFF<0.03", "")
    final_dir = np.where(forced, "OVER", final_dir)
    reason = np.where(forced, "FORCED_OVER_ONLY_GOB_DEM", reason)

    out["final_bet_direction"] = final_dir
    out["final_dir_reason"] = reason

    slate_d = str(args.date or "").strip()[:10]
    if len(slate_d) < 10:
        slate_d = ""
    from_start = pd.Series([""] * len(out), index=out.index, dtype=object)
    if "start_time" in out.columns:
        et = _start_times_et(out["start_time"])
        from_start = et.dt.strftime("%Y-%m-%d").where(et.notna(), "").astype(str).str.strip()
    prev_gd = out.get("game_date", pd.Series([""] * len(out))).astype(str).str.strip().str[:10]
    prev_ok = prev_gd.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
    # Prefer valid upstream game_date (step1 may anchor full boards to --date). Do not let
    # start_time ET overwrite it — that breaks combined_slate_tickets date filtering.
    merged = prev_gd.where(prev_ok, from_start.where(from_start.str.len() > 0, ""))
    merged = merged.where(merged.str.len() > 0, slate_d)
    out["game_date"] = merged.fillna("")

    # Save full CSV
    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}")
    print("final_bet_direction counts:")
    print(pd.Series(out["final_bet_direction"]).value_counts().to_string())
    if "tier" in out.columns:
        print("tier counts:")
        print(out["tier"].value_counts().to_string())

    # Save clean XLSX
    xlsx_path = args.xlsx if args.xlsx else args.output.replace(".csv", "_clean.xlsx")
    Path(xlsx_path).parent.mkdir(parents=True, exist_ok=True)
    build_clean_xlsx(out, xlsx_path)
    _copy_dated_step8_wnba(xlsx_path, (args.date or "").strip())

if __name__ == "__main__":
    main()
