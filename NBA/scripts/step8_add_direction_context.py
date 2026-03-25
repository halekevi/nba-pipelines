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
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import os
from datetime import datetime

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
        'Team': 6, 'Opp': 6, 'Game Time': 10,
        'Prop': 16, 'Pick Type': 10, 'Line': 7,
        'Direction': 9, 'Edge': 7, 'Projection': 10,
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
    df2['game_time'] = pd.to_datetime(df2.get('start_time', ''), errors='coerce').dt.strftime('%-I:%M %p')

    keep = [
        'tier', 'rank_score',
        'player', 'pos', 'team', 'opp_team', 'game_time',
        'prop_type', 'pick_type', 'line',
        'final_bet_direction',
        'edge', 'projection',
        'ml_prob',
        'line_hit_rate_over_ou_5',
        'stat_last5_avg', 'stat_season_avg',
        'last5_over', 'last5_under',
        'OVERALL_DEF_RANK', 'DEF_TIER',
        'minutes_tier', 'shot_role', 'usage_role',
        'void_reason',
        # ── Intel layer (step6e) ──────────────────────────────────────────
        'intel_season_avg', 'intel_l5_avg', 'intel_l10_avg',
        'intel_season_hit_rate', 'intel_cushion', 'intel_cv_pct',
        'intel_opp_vs_league_pct', 'intel_l5_vs_season',
        'intel_season_games',
        # ── Full game log (g1-g10) and H2H ───────────────────────────────
        'stat_g1', 'stat_g2', 'stat_g3', 'stat_g4', 'stat_g5',
        'stat_g6', 'stat_g7', 'stat_g8', 'stat_g9', 'stat_g10',
        'stat_last10_avg',
        'h2h_avg', 'h2h_over_rate', 'h2h_games_vs_opp', 'h2h_last_stat',
        'b2b_flag', 'days_rest', 'game_total', 'spread',
        'game_script_mult', 'game_script_note',
    ]
    # only keep cols that exist
    keep = [c for c in keep if c in df2.columns]
    clean = df2[keep].copy()

    for col in ['rank_score', 'edge', 'projection', 'ml_prob', 'line_hit_rate_over_ou_5']:
        if col in clean.columns:
            clean[col] = pd.to_numeric(clean[col], errors='coerce').round(4 if col == 'ml_prob' else 2)
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
        'player': 'Player', 'pos': 'Pos', 'team': 'Team', 'opp_team': 'Opp', 'game_time': 'Game Time',
        'prop_type': 'Prop', 'pick_type': 'Pick Type', 'line': 'Line',
        'final_bet_direction': 'Direction',
        'edge': 'Edge', 'projection': 'Projection',
        'ml_prob': 'ML Prob',
        'line_hit_rate_over_ou_5': 'Hit Rate (5g)',
        'stat_last5_avg': 'Last 5 Avg', 'stat_season_avg': 'Season Avg',
        'last5_over': 'L5 Over', 'last5_under': 'L5 Under',
        'OVERALL_DEF_RANK': 'Def Rank', 'DEF_TIER': 'Def Tier',
        'minutes_tier': 'Min Tier', 'shot_role': 'Shot Role', 'usage_role': 'Usage Role',
        'void_reason': 'Void Reason',
        # Intel columns
        'intel_season_avg':        'Intel Season Avg',
        'intel_l5_avg':            'Intel L5 Avg',
        'intel_l10_avg':           'Intel L10 Avg',
        'intel_season_hit_rate':   'Season Hit%',
        'intel_cushion':           'Cushion',
        'intel_cv_pct':            'CV%',
        'intel_opp_vs_league_pct': 'Opp vs Avg%',
        'intel_l5_vs_season':      'L5 vs Season',
        'intel_season_games':      'Season GP',
        # Game log
        'stat_last10_avg':         'Last 10 Avg',
        'stat_g1': 'G1', 'stat_g2': 'G2', 'stat_g3': 'G3',
        'stat_g4': 'G4', 'stat_g5': 'G5', 'stat_g6': 'G6',
        'stat_g7': 'G7', 'stat_g8': 'G8', 'stat_g9': 'G9', 'stat_g10': 'G10',
        # H2H
        'h2h_avg':          'H2H Avg',
        'h2h_over_rate':    'H2H Over%',
        'h2h_games_vs_opp': 'H2H Games',
        'h2h_last_stat':    'H2H Last',
        # Schedule
        'b2b_flag':  'B2B',
        'days_rest': 'Days Rest',
        'game_total':'Game Total',
        'spread':    'Spread',
        'game_script_mult': 'Game Script Mult',
        'game_script_note': 'Game Script Note',
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
    ap.add_argument("--date", default="", help="Filter to YYYY-MM-DD based on start_time")
    args = ap.parse_args()

    print(f"→ Loading: {args.input} (sheet={args.sheet})")
    df = pd.read_excel(args.input, sheet_name=args.sheet, dtype=str).fillna("")

    out = df.copy()

    # Keep only rows for target slate date when start_time is available.
    # This prevents stale historical slates from leaking into today's output.
    if "start_time" in out.columns:
        target_date = (args.date or datetime.now().strftime("%Y-%m-%d")).strip()
        start_dt = pd.to_datetime(out["start_time"], errors="coerce")
        start_dates = start_dt.dt.strftime("%Y-%m-%d")
        keep_mask = start_dates.eq(target_date)
        kept = int(keep_mask.sum())
        total = len(out)
        if kept == 0:
            # Fallback to latest available slate date so pipeline does not emit empty NBA outputs
            available_dates = start_dates[start_dates.notna() & (start_dates != "NaT")]
            if len(available_dates):
                fallback_date = available_dates.max()
                keep_mask = start_dates.eq(fallback_date)
                kept = int(keep_mask.sum())
                print(
                    f"[DateFilter] No rows for {target_date}; "
                    f"falling back to latest available date {fallback_date} ({kept} rows)"
                )
            else:
                print(f"[DateFilter] No parseable start_time values; keeping all {total} rows")
                keep_mask = pd.Series(True, index=out.index)
        else:
            print(f"[DateFilter] Kept {kept}/{total} rows for {target_date} (dropped {total - kept} rows)")
        out = out.loc[keep_mask].copy()

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
    build_clean_xlsx(out, xlsx_path)

if __name__ == "__main__":
    main()
