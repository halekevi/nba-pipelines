#!/usr/bin/env python3
"""
Full Slate Grader v4 — NBA/CBB with full breakdowns:
- Summary: Overall, By Pick Type (Standard→OVER/UNDER), By Tier (→pick types→OVER/UNDER),
           By Def Tier, By Minutes Tier, By Player Role — all with OVER/UNDER splits
- Sheets: Box Raw, By Pick Type, By Tier, Prop Type x Direction,
          By Direction, By Edge Bucket, By Minutes Tier, By Def Tier,
          By Player Role, By Shot Role, Void Reasons
"""
import pandas as pd
import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from player_name_norm import fold_player_name  # noqa: E402
from utils.slate_fields import (  # noqa: E402
    first_numeric_in_slate_row,
    first_over_under_in_slate_row,
)


def _def_rank_bucket(x):
    try:
        r = float(x)
    except Exception:
        return "UNK"
    if r <= 5:   return "01-05"
    if r <= 10:  return "06-10"
    if r <= 20:  return "11-20"
    if r <= 25:  return "21-25"
    return "26-30"

C = {
    'hit':'27AE60','miss':'E74C3C','push':'F39C12','void':'95A5A6',
    'hdr':'1C1C1C','hdr2':'1A5276','hdr3':'1E8449','hdr4':'7D6608',
    'hdr5':'922B21','hdr6':'6C3483','hdr7':'117A65','hdr8':'1A5276',
    'alt':'F2F3F4','white':'FFFFFF',
    'tier_a':'D5F5E3','tier_b':'D6EAF8','tier_c':'FEF9E7','tier_d':'FDEDEC',
    'over':'D6EAF8','under':'FDEBD0',
    'goblin':'E8D5F5','demon':'FDEDEC','standard':'F2F3F4',
}
DEF_TIER_ORDER    = ['Elite','Above Avg','Avg','Weak']
MINUTES_TIER_ORDER= ['HIGH','MEDIUM','LOW','UNKNOWN']
USAGE_ROLE_ORDER  = ['PRIMARY','SECONDARY','SUPPORT','UNKNOWN']
SHOT_ROLE_ORDER   = ['HIGH_VOL','MID_VOL','LOW_VOL','UNKNOWN']
TIER_ORDER        = ['A','B','C','D']

def bdr(color='CCCCCC'):
    s = Side(style='thin',color=color)
    return Border(left=s,right=s,top=s,bottom=s)

def hc(ws,r,c,v,bg=None,fc='FFFFFF',bold=True,sz=9,align='center'):
    cell=ws.cell(row=r,column=c,value=v)
    cell.font=Font(bold=bold,color=fc,name='Arial',size=sz)
    if bg: cell.fill=PatternFill('solid',start_color=bg)
    cell.alignment=Alignment(horizontal=align,vertical='center')
    cell.border=bdr()
    return cell

def dc(ws,r,c,v,bg=None,bold=False,sz=9,align='center',fmt=None,fc='000000'):
    cell=ws.cell(row=r,column=c,value=v)
    cell.font=Font(bold=bold,name='Arial',size=sz,color=fc)
    cell.fill=PatternFill('solid',start_color=bg or C['white'])
    cell.alignment=Alignment(horizontal=align,vertical='center')
    cell.border=bdr()
    if fmt: cell.number_format=fmt
    return cell

def res_bg(r):
    return {'HIT':C['hit'],'MISS':C['miss'],'PUSH':C['push'],'VOID':C['void']}.get(str(r).upper(),'DDDDDD')

def hr_bg(v):
    if v is None or (isinstance(v,float) and np.isnan(v)): return 'DDDDDD'
    if v>=0.65: return C['hit']
    if v>=0.50: return C['push']
    return C['miss']

def tier_bg(t):
    return {'A':C['tier_a'],'B':C['tier_b'],'C':C['tier_c'],'D':C['tier_d']}.get(str(t).upper(),C['white'])

def pct_cell(ws,r,c,val):
    nan=val is None or (isinstance(val,float) and np.isnan(val))
    bg=hr_bg(val) if not nan else 'DDDDDD'
    cell=dc(ws,r,c,val if not nan else '',bg=bg,bold=True)
    if not nan:
        cell.number_format='0.0%'
        cell.font=Font(bold=True,name='Arial',size=9,color='FFFFFF')
    return cell

def hit_rate(sub):
    # Demons are excluded from hit-rate grading (data-collection only).
    # They still appear in Box Raw but are NOT counted in any hit-rate summary.
    graded = sub[sub['pick_type'] != 'Demon'] if 'pick_type' in sub.columns else sub
    dec = graded[graded['result'].isin(['HIT', 'MISS'])]
    h = (dec['result'] == 'HIT').sum()
    # Voids = non-decided graded rows + all Demon rows (they don't count toward the rate)
    demon_count = int((sub['pick_type'] == 'Demon').sum()) if 'pick_type' in sub.columns else 0
    v = int(graded['result'].isin(['VOID', 'PUSH']).sum()) + demon_count
    return (h / len(dec) if len(dec) else np.nan), int(h), int(len(dec) - h), v, int(len(dec))

def drc(df):
    return 'bet_direction' if 'bet_direction' in df.columns else 'final_bet_direction'

def sw(ws,widths):
    for ci,w in enumerate(widths,1):
        ws.column_dimensions[get_column_letter(ci)].width=w

def sheet_hdr8(ws,col1,bg,widths=None):
    sw(ws,widths or [24,10,8,10,8,8,8,12])
    for ci,h in enumerate([col1,'Direction','Total','Decided','Hits','Misses','Voids','Hit Rate'],1):
        hc(ws,1,ci,h,bg=bg)
    ws.row_dimensions[1].height=20
    ws.freeze_panes='A2'

def write_dir_subrows(ws,ri,df,label,bg,ncols=8):
    d=drc(df)
    hr_a,h_a,m_a,v_a,dec_a=hit_rate(df)
    row_bg=bg or (C['alt'] if ri%2==0 else C['white'])
    dc(ws,ri,1,label,bg=row_bg,bold=True,align='left')
    dc(ws,ri,2,'ALL',bg=row_bg,bold=True)
    dc(ws,ri,3,len(df),bg=row_bg); dc(ws,ri,4,dec_a,bg=row_bg)
    dc(ws,ri,5,h_a,bg=row_bg); dc(ws,ri,6,m_a,bg=row_bg); dc(ws,ri,7,v_a,bg=row_bg)
    pct_cell(ws,ri,8,hr_a); ri+=1
    for direction in ['OVER','UNDER']:
        dsub=df[df[d].str.upper()==direction]
        if len(dsub)==0: continue
        hr_d,h_d,m_d,v_d,dec_d=hit_rate(dsub)
        dbg=C['over'] if direction=='OVER' else C['under']
        dc(ws,ri,1,'',bg=dbg); dc(ws,ri,2,direction,bg=dbg,bold=True)
        dc(ws,ri,3,len(dsub),bg=dbg); dc(ws,ri,4,dec_d,bg=dbg)
        dc(ws,ri,5,h_d,bg=dbg); dc(ws,ri,6,m_d,bg=dbg); dc(ws,ri,7,v_d,bg=dbg)
        pct_cell(ws,ri,8,hr_d); ri+=1
    return ri

# ── Grade ─────────────────────────────────────────────────────────────────────
def _row_first_numeric(row, keys):
    """
    First non-null numeric among named columns (pandas Series or mapping).
    Delegates to utils.slate_fields for Series; dict-like rows use .get chain.
    """
    if isinstance(row, pd.Series):
        return first_numeric_in_slate_row(row, tuple(keys))
    for k in keys:
        if not hasattr(row, "get"):
            break
        x = pd.to_numeric(row.get(k, np.nan), errors="coerce")
        if pd.notna(x):
            return float(x)
    return np.nan


def _row_bet_direction(row) -> str:
    keys = (
        "bet_direction",
        "final_bet_direction",
        "Direction",
        "direction",
        "recommended_side",
    )
    if isinstance(row, pd.Series):
        v = first_over_under_in_slate_row(row, keys)
        return v if v else "OVER"
    if hasattr(row, "get"):
        for k in keys:
            val = row.get(k)
            s = str(val if val is not None else "").strip().upper()
            if s in ("OVER", "UNDER"):
                return s
    return "OVER"


def grade(row, actual):
    """Return (result, void_reason_or_None, margin). Margin is NaN when ungraded."""
    act = pd.to_numeric(actual, errors="coerce")
    if pd.isna(act):
        return "VOID", "NO_ACTUAL", np.nan
    actual_f = float(act)

    line = _row_first_numeric(row, ("line", "Line", "line_score", "LINE", "main_line"))
    if pd.isna(line):
        return "VOID", "NO_LINE", np.nan
    line = float(line)

    direction = _row_bet_direction(row)
    if actual_f == line:
        return "VOID", "PUSH", 0.0
    if direction == "OVER":
        result = "HIT" if actual_f > line else "MISS"
    else:
        result = "HIT" if actual_f < line else "MISS"
    m = round(actual_f - line if direction == "OVER" else line - actual_f, 2)
    return result, None, m

PROP_NORM_MAP={
    # short code aliases (common in CBB pipeline files)
    'pts':'points',
    'reb':'rebounds',
    'ast':'assists',
    'stl':'steals',
    'blk':'blocked shots',
    'tov':'turnovers',
    'fg3m':'3-pt made',
    'pr':'pts+rebs',
    'pa':'pts+asts',
    'ra':'rebs+asts',
    'stocks':'blks+stls',
    'pts+rebs+asts':'pts+rebs+asts','pra':'pts+rebs+asts',
    'pts+rebs':'pts+rebs','pts+asts':'pts+asts','rebs+asts':'rebs+asts','blks+stls':'blks+stls',
    'blocked shots':'blocked shots','blocks':'blocked shots',
    'steals':'steals','turnovers':'turnovers',
    # FG Made / Attempted
    'fg made':'fg made','field goals made':'fg made','fgm':'fg made',
    'fg attempted':'fg attempted','field goals attempted':'fg attempted','fga':'fg attempted',
    # 3-PT
    '3-pt made':'3-pt made','3pt made':'3-pt made','3pm':'3-pt made',
    '3 pt made':'3-pt made','3-point made':'3-pt made','3 point made':'3-pt made',
    'three pointers made':'3-pt made','three pointer made':'3-pt made',
    '3-pt attempted':'3-pt attempted','3pt attempted':'3-pt attempted','3pa':'3-pt attempted',
    '3 pt attempted':'3-pt attempted','3-point attempted':'3-pt attempted','3 point attempted':'3-pt attempted',
    'three pointers attempted':'3-pt attempted','three pointer attempted':'3-pt attempted',
    # Two Pointers
    'two pointers made':'two pointers made','2-pt made':'two pointers made',
    '2pt made':'two pointers made','two pointer made':'two pointers made',
    'two pointers attempted':'two pointers attempted','2-pt attempted':'two pointers attempted',
    '2pt attempted':'two pointers attempted','two pointer attempted':'two pointers attempted',
    # Free Throws
    'free throws made':'free throws made','ftm':'free throws made',
    'free throw made':'free throws made',
    'free throws attempted':'free throws attempted','fta':'free throws attempted',
    'free throw attempted':'free throws attempted',
    # Other
    'offensive rebounds':'offensive rebounds','defensive rebounds':'defensive rebounds',
    'personal fouls':'personal fouls','fantasy score':'fantasy score',
    # Milestone yes/no (actuals from box: 1.0 / 0.0 vs typical 0.5 line)
    'double double':'double double','triple double':'triple double',
    'double-double':'double double','triple-double':'triple double',
    'dd':'double double','td':'triple double',
    # Combo props — display names from PP slate files
    'points (combo)':'points',
    'assists (combo)':'assists',
    'rebounds (combo)':'rebounds',
    '3-pt made (combo)':'3-pt made',
    '3-pt attempted (combo)':'3-pt attempted',
    'two pointers made (combo)':'two pointers made',
    'two pointers attempted (combo)':'two pointers attempted',
    'free throws made (combo)':'free throws made',
    'free throws attempted (combo)':'free throws attempted',
    '3pt made (combo)':'3-pt made',
    '3pt attempted (combo)':'3-pt attempted',
    # Alternate display-name capitalizations seen in slate exports
    'field goal attempts':'fg attempted',
    'field goals':'fg made',
    '3-pointers made':'3-pt made',
    '3-pointers attempted':'3-pt attempted',
    '3 pointers made':'3-pt made',
    '3 pointers attempted':'3-pt attempted',
    # NBA step7 prop_norm short codes (align with fetch_actuals Title Case → lower)
    'fg3m':'3-pt made',
    'fg3a':'3-pt attempted',
    'fg2m':'two pointers made',
    'fg2a':'two pointers attempted',
    'fgm':'fg made',
    'fga':'fg attempted',
    'ftm':'free throws made',
    'fta':'free throws attempted',
    'oreb':'offensive rebounds',
    'orebs':'offensive rebounds',
    'dreb':'defensive rebounds',
    'drebs':'defensive rebounds',
    'pf':'personal fouls',
    'personalfouls':'personal fouls',
    'fantasy':'fantasy score',
    'fantasyscore':'fantasy score',
    'fs':'fantasy score',
    '2ptm':'two pointers made',
    '2pta':'two pointers attempted',
    '2pt made':'two pointers made',
    '2pt attempted':'two pointers attempted',
}
def norm_prop_key(p) -> str:
    s = str(p).lower().strip()
    # normalize common suffixes
    s = s.replace("(combo)", "").strip()
    # collapse whitespace
    s = " ".join(s.split())
    return PROP_NORM_MAP.get(s, s)


def norm_player_key(p) -> str:
    return fold_player_name(p)


# Columns used to build ``player|prop`` keys when joining slate rows to actuals.
_PROP_RESOLVE_COLS: tuple[str, ...] = (
    "prop_type_norm",
    "stat_norm",
    "prop_norm",
    "prop_type",
    "Prop",
)


def _alias_cols(df):
    for alias,canon in [('DEF_TIER','def_tier'),('minutes_tier','minutes_tier'),
                         ('shot_role','shot_role'),('usage_role','usage_role')]:
        if alias in df.columns and canon not in df.columns:
            df[canon]=df[alias]
    return df


def _coalesce_line_from_line_score(df):
    """Fill NaN line values from line_score (NBA/CBB exports often split these)."""
    if "line_score" not in df.columns:
        return df
    ls = pd.to_numeric(df["line_score"], errors="coerce")
    if "line" not in df.columns:
        df["line"] = ls
    else:
        ln = pd.to_numeric(df["line"], errors="coerce")
        df["line"] = ln.where(ln.notna(), ls)
    return df


def _coalesce_line_from_projection(df):
    """When book line is blank, use numeric projection / model line (step7 exports)."""
    for col in ("projection", "Projection", "proj", "model_line", "consensus_line"):
        if col not in df.columns:
            continue
        pv = pd.to_numeric(df[col], errors="coerce")
        if pv.notna().sum() == 0:
            continue
        if "line" not in df.columns:
            df["line"] = pv
        else:
            ln = pd.to_numeric(df["line"], errors="coerce")
            df["line"] = ln.where(ln.notna(), pv)
    return df


def load_nba(path: str) -> pd.DataFrame:
    xls = pd.ExcelFile(path, engine="openpyxl")
    preferred = ["ALL", "Box Raw", "Props", "Sheet1"]
    sheet = next((s for s in preferred if s in xls.sheet_names), xls.sheet_names[0])
    if sheet != "ALL":
        print(f"⚠️ NBA: sheet 'ALL' not found in {os.path.basename(path)}. Using sheet='{sheet}'.")

    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    # --- Normalize column names from PipelineA output ---
    df.columns = [str(c).strip() for c in df.columns]

    df = df.rename(columns={
    "Player": "player",
    "Prop": "prop_type_norm",
    "Pick Type": "pick_type",
    "Line": "line",
    "Direction": "bet_direction",
    "Team": "team",
    "Opp": "opp_team",
    "Pos": "pos",
    "Tier": "tier",
    "Game Time": "game_time",
    "Rank Score": "rank_score",
    "Edge": "edge",
    "Projection": "projection",
    "Void Reason": "void_reason",

    # ADD THESE 👇
    "Def Rank": "def_rank",
    "Def Tier": "def_tier",
    "Min Tier": "minutes_tier",
    "Shot Role": "shot_role",
    "Usage Role": "usage_role",
    "ML Prob": "ml_prob",
    "ml_prob": "ml_prob",
    "ML Edge": "ml_edge",
    "ml_edge": "ml_edge",
    "Edge Score": "edge_score",
    "edge_score": "edge_score",
    "Blended Score": "blended_score",
    "blended_score": "blended_score",
})
    # fallback compatibility
    if "prop_type_norm" not in df.columns:
        df["prop_type_norm"] = df.get("prop_type", "")

    if "bet_direction" not in df.columns and "final_bet_direction" in df.columns:
        df["bet_direction"] = df["final_bet_direction"]

    if "ml_prob" in df.columns:
        df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")
    if "ml_edge" in df.columns:
        df["ml_edge"] = pd.to_numeric(df["ml_edge"], errors="coerce")
    elif "ml_prob" in df.columns:
        df["ml_edge"] = df["ml_prob"] - 0.5

    _coalesce_line_from_line_score(df)
    _coalesce_line_from_projection(df)

    # Hard fail if player still missing
    if "player" not in df.columns:
        raise KeyError(f"NBA slate missing 'player' column. Found columns: {list(df.columns)}")
    # Match CBB/apply_actuals: suffix-stripped, punctuation-normalized keys reduce false NO_ACTUAL.
    df["player_key"] = (
        df["player"].astype(str).apply(norm_player_key)
        + "|"
        + df["prop_type_norm"].apply(norm_prop_key)
    )
    return df


def filter_nba_slate_by_grade_date(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    Keep only rows whose game date matches date_str (YYYY-MM-DD).
    Stops NBA1H/NBA1Q/full NBA from grading the wrong slate when only
    NBA\\step8_*_clean.xlsx (latest pipeline) is available.
    """
    if not date_str or not len(df):
        return df
    col = None
    for c in ("game_time", "game_start", "fetched_at"):
        if c in df.columns:
            col = c
            break
    if not col:
        return df
    ts = pd.to_datetime(df[col], errors="coerce")
    try:
        target = pd.to_datetime(date_str).date()
    except Exception:
        return df
    row_days = ts.dt.date
    mask = row_days == target
    if not mask.any():
        if ts.notna().sum() == 0:
            print(
                f"  WARN: No parseable dates in '{col}'; cannot filter to {date_str} -- using all {len(df)} rows."
            )
            return df
        print(
            f"  WARN: Date filter {date_str} on '{col}': 0 rows match -- "
            f"graded workbook will be empty (slate is for other day(s))."
        )
        print(
            "  HINT: Use run_grader.ps1 -Date <game-day> matching Game Time in the slate, "
            "or place that day's step8 workbook under outputs\\<date>\\ for extraction."
        )
        return df.loc[mask].copy()
    print(f"  INFO: Slate date filter ({date_str}): kept {int(mask.sum())}/{len(df)} rows")
    return df.loc[mask].copy()


def load_cbb(path: str) -> pd.DataFrame:
    # CBB graded files sometimes have 'ALL', but sometimes just Sheet1
    xls = pd.ExcelFile(path, engine="openpyxl")
    sheet = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
    if sheet != "ALL":
        print(f"⚠️ CBB: sheet 'ALL' not found in {os.path.basename(path)}. Using sheet='{sheet}'.")
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]

    # ── FIX: rename CBB pipeline column names to grader-expected names ──────────
    df = df.rename(columns={
        "Player":              "player",
        "Line":                "line",
        "Void Reason":         "void_reason",
        "opp_team_abbr":       "opp_team",
        "team_abbr":           "team",
        "prop_norm":           "prop_type_norm",   # FIX 2: use normalized prop, not verbose prop_type
        "final_bet_direction": "bet_direction",
        "model_dir_5":         "model_dir_5",
        "opp_def_tier":        "def_tier",          # prefer opp_def_tier if def_tier blank
        "OVERALL_DEF_RANK":    "def_rank",
        "rank_score":          "rank_score",
        "edge":                "edge",
        "ML Prob":             "ml_prob",
        "ml_prob":             "ml_prob",
        "ML Edge":             "ml_edge",
        "ml_edge":             "ml_edge",
        "Edge Score":          "edge_score",
        "edge_score":          "edge_score",
        "Blended Score":       "blended_score",
        "blended_score":       "blended_score",
    })
    # Guard against duplicate column names after renames (e.g. bet_direction).
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]

    df = _alias_cols(df)

    # FIX 1: bet_direction — CBB step3 has no direction column yet; default to OVER
    # but warn so the user knows actuals grading requires a direction column.
    if "bet_direction" not in df.columns:
        dir_candidates = [c for c in df.columns if "direction" in c.lower() or "dir" in c.lower()]
        if dir_candidates:
            df["bet_direction"] = df[dir_candidates[0]].astype(str).str.upper().str.strip()
            print(f"  ℹ️  CBB: using '{dir_candidates[0]}' as bet_direction")
        else:
            print("  ⚠️  CBB: no direction column found — all props will default to OVER. "
                  "Pass a slate file that includes final_bet_direction for correct grading.")
            df["bet_direction"] = "OVER"

    # FIX 2: prop_type_norm — use prop_norm (already normalized) not verbose prop_type
    if "prop_type_norm" not in df.columns:
        if "prop_norm" in df.columns:
            df["prop_type_norm"] = df["prop_norm"]
        else:
            df["prop_type_norm"] = df.get("prop_type", "")

    # FIX 3: tier — may not exist in early pipeline steps; add empty column so
    # downstream breakdown sheets don't silently skip all rows.
    if "tier" not in df.columns:
        print("  ⚠️  CBB: no 'tier' column found — By Tier breakdowns will be empty. "
              "Pass step6_ranked_cbb.xlsx for full tier grading.")
        df["tier"] = ""

    if "ml_prob" in df.columns:
        df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")
    if "ml_edge" in df.columns:
        df["ml_edge"] = pd.to_numeric(df["ml_edge"], errors="coerce")
    elif "ml_prob" in df.columns:
        df["ml_edge"] = df["ml_prob"] - 0.5
    for c in ("edge_score", "blended_score"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    _coalesce_line_from_line_score(df)
    _coalesce_line_from_projection(df)

    # Standardized key used to join actuals
    df["player_key"] = df["player"].astype(str).apply(norm_player_key) + "|" + df["prop_type_norm"].apply(norm_prop_key)
    return df

def _build_actuals_lookup(act: pd.DataFrame) -> dict[str, float]:
    """Map ``norm_player|norm_prop`` -> actual, plus alias keys from PROP_NORM_MAP.

    Period and full-game actuals CSVs use PrizePicks-style labels (``Points``,
    ``3-PT Made``) while some slates use short codes (``pts``, ``fg3m``). Register
    every alias that normalizes to the same canonical prop so rows are not voided
    as ``NO_ACTUAL`` when the game was played and the stat exists under a variant
    label.
    """
    out: dict[str, float] = {}
    if act is None or len(act) == 0:
        return out
    if "player" not in act.columns or "actual" not in act.columns:
        return out
    prop_col = "prop_type" if "prop_type" in act.columns else ("Prop" if "Prop" in act.columns else None)
    if not prop_col:
        return out
    for _, arow in act.iterrows():
        val = pd.to_numeric(arow.get("actual"), errors="coerce")
        if pd.isna(val):
            continue
        p0 = norm_player_key(str(arow.get("player", "") or ""))
        if not p0:
            continue
        raw_prop = str(arow.get(prop_col, "") or "").strip()
        if not raw_prop or raw_prop.lower() in ("nan", "none"):
            continue
        canon = norm_prop_key(raw_prop)
        keys: set[str] = {f"{p0}|{canon}"}
        for short, mapped in PROP_NORM_MAP.items():
            ns = norm_prop_key(short)
            nl = norm_prop_key(mapped)
            if nl == canon or ns == canon:
                keys.add(f"{p0}|{ns}")
                keys.add(f"{p0}|{nl}")
        for k in keys:
            if not k.endswith("|") and "|" in k:
                out[k] = float(val)
    return out


def _resolve_actual(act_map: dict[str, float], row) -> float:
    """Primary player_key on row, then alternate stat columns, then combo sum."""
    key = row.get("player_key", "")
    if isinstance(key, str) and key.strip() and key.strip().lower() not in ("nan", "none"):
        a = act_map.get(key, np.nan)
        if pd.notna(a):
            return float(a)
    player = str(row.get("player", "") or "")
    p0 = norm_player_key(player)
    if p0:
        for col in _PROP_RESOLVE_COLS:
            if col not in row.index:
                continue
            cell = row.get(col)
            if cell is None or (isinstance(cell, float) and pd.isna(cell)):
                continue
            nk = f"{p0}|{norm_prop_key(str(cell))}"
            a = act_map.get(nk, np.nan)
            if pd.notna(a):
                return float(a)
    return np.nan


def _resolve_actual_for_player(act_map: dict[str, float], player_str: str, row) -> float:
    """Resolve actual for one combo leg using the same prop columns as ``_resolve_actual``."""
    p0 = norm_player_key(str(player_str or ""))
    if not p0:
        return np.nan
    for col in _PROP_RESOLVE_COLS:
        if col not in row.index:
            continue
        cell = row.get(col)
        if cell is None or (isinstance(cell, float) and pd.isna(cell)):
            continue
        nk = f"{p0}|{norm_prop_key(str(cell))}"
        a = act_map.get(nk, np.nan)
        if pd.notna(a):
            return float(a)
    return np.nan


def _split_combo_players(player_str: str) -> list[str]:
    s = str(player_str)
    # common separators in PP combo names
    for sep in [" + ", "+", " & ", "&", " / ", "/", " vs ", " and "]:
        if sep in s:
            parts = [p.strip() for p in s.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts
    return []

def apply_actuals(df, actuals_path):
    act = pd.read_csv(actuals_path)
    act_map = _build_actuals_lookup(act)

    df["player_key"] = (
        df["player"].astype(str).apply(norm_player_key)
        + "|"
        + df["prop_type_norm"].fillna("").astype(str).apply(norm_prop_key)
    )

    # Diagnostic: surface any slate prop types with zero actuals matches so mismatches are visible
    act_props = {str(k).split("|", 1)[-1] for k in act_map.keys()}
    unmatched_props = set()
    for key in df["player_key"]:
        key = str(key) if key is not None else ""
        if not key or key in ("nan", "none"): continue
        prop_part = key.split("|", 1)[-1]
        if key not in act_map and prop_part not in act_props:
            unmatched_props.add(prop_part)
    if unmatched_props:
        print(f"  ⚠️  Prop types in slate with NO actuals matches: {sorted(unmatched_props)}")

    # Upstream void_reason (NBA step7, etc.) marks eligibility / strategy filters
    # (BLOCKED_STD_OVER_LOW_HR, DROPPED_NEG_EDGE_GOBDEM, NO_PROJECTION_OR_LINE, …).
    # It must NOT force VOID when we have a real line + box-score actual — otherwise
    # Prop Evaluation and archives show Actual filled but Result VOID and Margin empty.

    results, void_reasons, margins, actuals_out = [], [], [], []
    for _, row in df.iterrows():
        key = row.get("player_key", "")
        if not isinstance(key, str) or key.strip() in ("", "nan", "none"):
            key = ""
        actual = _resolve_actual(act_map, row)

        # If this is a combo prop (Points (Combo), etc.), try to compute combined actual
        if pd.isna(actual):
            parts = _split_combo_players(row.get("player", ""))
            if parts:
                vals = []
                ok = True
                for p in parts:
                    v = _resolve_actual_for_player(act_map, p, row)
                    if pd.isna(v):
                        ok = False
                        break
                    vals.append(float(v))
                if ok and vals:
                    actual = float(sum(vals))

        actuals_out.append(actual)

        void_r = row.get("void_reason", np.nan)
        void_r_str = str(void_r).strip() if pd.notna(void_r) else ""
        upstream = void_r_str if void_r_str not in ("", "nan") else ""

        r, vr, m = grade(row, actual)
        decided = r in ("HIT", "MISS") or (
            r == "VOID" and str(vr or "").upper() == "PUSH"
        )

        if decided:
            results.append(r)
            margins.append(m)
            parts = [upstream] if upstream else []
            if vr:
                parts.append(str(vr))
            void_reasons.append("; ".join(parts))
        else:
            results.append("VOID")
            margins.append(np.nan)
            tail = str(vr or "").strip()
            if upstream and tail:
                void_reasons.append(f"{upstream}; {tail}")
            elif upstream:
                void_reasons.append(upstream)
            else:
                void_reasons.append(tail or "NO_ACTUAL")

    df["actual"] = actuals_out
    df["result"] = results
    df["void_reason_grade"] = void_reasons
    df["margin"] = margins
    df["result_sign"] = df["result"].map({"HIT": 1, "MISS": -1, "VOID": 0, "PUSH": 0})
    return df

def breakdown(df,group_col):
    rows=[]
    for key,sub in df.groupby(group_col,dropna=False):
        hr,h,m,v,dec=hit_rate(sub)
        rows.append({group_col:key,'total':len(sub),'hit':h,'miss':m,'void':v,'decided':dec,
                     'hit_rate':round(hr,4) if not np.isnan(hr) else np.nan})
    if not rows:
        return pd.DataFrame(columns=[group_col,'total','hit','miss','void','decided','hit_rate'])
    return pd.DataFrame(rows).sort_values('total',ascending=False)

def write_flat_breakdown(wb,df_b,sheet_name,group_col,bg_hdr):
    ws=wb.create_sheet(sheet_name)
    sw(ws,[28,8,8,8,8,10,12])
    for ci,col in enumerate([group_col,'total','hit','miss','void','decided','hit_rate'],1):
        hc(ws,1,ci,col,bg=bg_hdr)
    ws.row_dimensions[1].height=20; ws.freeze_panes='A2'
    for ri,row in enumerate(df_b.itertuples(),2):
        bg=C['alt'] if ri%2==0 else C['white']
        hr=getattr(row,'hit_rate',np.nan)
        try: gval=getattr(row,group_col)
        except: gval=getattr(row,group_col.replace(' ','_').replace('-','_').replace('+','_'),'')
        dc(ws,ri,1,gval,bg=bg,align='left')
        dc(ws,ri,2,getattr(row,'total',0),bg=bg); dc(ws,ri,3,getattr(row,'hit',0),bg=bg)
        dc(ws,ri,4,getattr(row,'miss',0),bg=bg); dc(ws,ri,5,getattr(row,'void',0),bg=bg)
        dc(ws,ri,6,getattr(row,'decided',0),bg=bg); pct_cell(ws,ri,7,hr)

# ── By Pick Type sheet ────────────────────────────────────────────────────────
def write_pick_type_sheet(wb,df):
    ws=wb.create_sheet('By Pick Type')
    sheet_hdr8(ws,'Pick Type',C['hdr3'],[20,10,8,10,8,8,8,12])
    ri=2
    for pt in ['Goblin','Demon','Standard']:
        sub=df[df['pick_type']==pt]
        if len(sub)==0: continue
        pt_bg={'Goblin':C['goblin'],'Demon':C['demon'],'Standard':C['standard']}.get(pt,C['white'])
        if pt=='Standard':
            ri=write_dir_subrows(ws,ri,sub,pt,pt_bg)
        else:
            hr_a,h_a,m_a,v_a,dec_a=hit_rate(sub)
            dc(ws,ri,1,pt,bg=pt_bg,bold=True,align='left'); dc(ws,ri,2,'OVER',bg=pt_bg,bold=True)
            dc(ws,ri,3,len(sub),bg=pt_bg); dc(ws,ri,4,dec_a,bg=pt_bg)
            dc(ws,ri,5,h_a,bg=pt_bg); dc(ws,ri,6,m_a,bg=pt_bg); dc(ws,ri,7,v_a,bg=pt_bg)
            pct_cell(ws,ri,8,hr_a); ri+=1

# ── By Tier sheet ─────────────────────────────────────────────────────────────
def write_tier_sheet(wb,df):
    ws=wb.create_sheet('By Tier')
    sw(ws,[8,12,10,8,10,8,8,8,12])
    for ci,h in enumerate(['Tier','Pick Type','Direction','Total','Decided','Hits','Misses','Voids','Hit Rate'],1):
        hc(ws,1,ci,h,bg=C['hdr4'])
    ws.row_dimensions[1].height=20; ws.freeze_panes='A2'
    d=drc(df); ri=2

    def r9(ws,ri,c1,c2,c3,total,dec,h,m,v,hr_val,bg):
        dc(ws,ri,1,c1,bg=bg,bold=True,align='center')
        dc(ws,ri,2,c2,bg=bg,bold=True,align='left')
        dc(ws,ri,3,c3,bg=bg,bold=True)
        dc(ws,ri,4,total,bg=bg); dc(ws,ri,5,dec,bg=bg)
        dc(ws,ri,6,h,bg=bg); dc(ws,ri,7,m,bg=bg); dc(ws,ri,8,v,bg=bg)
        pct_cell(ws,ri,9,hr_val); return ri+1

    for t in TIER_ORDER:
        tsub=df[df['tier'].astype(str).str.upper()==t]
        if len(tsub)==0: continue
        tbg=tier_bg(t)
        hr_a,h_a,m_a,v_a,dec_a=hit_rate(tsub)
        ri=r9(ws,ri,f'Tier {t}','ALL','ALL',len(tsub),dec_a,h_a,m_a,v_a,hr_a,tbg)
        for pt in ['Goblin','Demon','Standard']:
            ptsub=tsub[tsub['pick_type']==pt] if 'pick_type' in tsub.columns else pd.DataFrame()
            if len(ptsub)==0: continue
            pt_bg={'Goblin':C['goblin'],'Demon':C['demon'],'Standard':C['standard']}.get(pt,C['alt'])
            hr_p,h_p,m_p,v_p,dec_p=hit_rate(ptsub)
            if pt=='Standard':
                ri=r9(ws,ri,'',pt,'ALL',len(ptsub),dec_p,h_p,m_p,v_p,hr_p,pt_bg)
                for direction in ['OVER','UNDER']:
                    dsub=ptsub[ptsub[d].str.upper()==direction]
                    if len(dsub)==0: continue
                    hr_d,h_d,m_d,v_d,dec_d=hit_rate(dsub)
                    dbg=C['over'] if direction=='OVER' else C['under']
                    ri=r9(ws,ri,'','',direction,len(dsub),dec_d,h_d,m_d,v_d,hr_d,dbg)
            else:
                ri=r9(ws,ri,'',pt,'OVER',len(ptsub),dec_p,h_p,m_p,v_p,hr_p,pt_bg)

# ── Prop Type x Direction sheet ───────────────────────────────────────────────
def write_prop_direction_sheet(wb,df):
    ws=wb.create_sheet('Prop Type x Direction')
    sheet_hdr8(ws,'Prop Type',C['hdr2'],[28,10,8,10,8,8,8,12])
    d=drc(df)
    pt_col='prop_type_norm' if 'prop_type_norm' in df.columns else 'prop_type'
    prop_order=(df[df['result'].isin(['HIT','MISS'])].groupby(pt_col).size()
                .sort_values(ascending=False).index.tolist())
    prop_order+=[p for p in df[pt_col].unique() if p not in prop_order]
    ri=2
    for prop in prop_order:
        psub=df[df[pt_col]==prop]
        ri=write_dir_subrows(ws,ri,psub,prop,C['alt'] if ri%2==0 else C['white'])

# ── Tier-with-direction generic sheet ────────────────────────────────────────
def write_tier_dir_sheet(wb,df,sheet_name,tier_col,tier_order,bg_hdr):
    if tier_col not in df.columns: return
    ws=wb.create_sheet(sheet_name)
    sheet_hdr8(ws,sheet_name.replace('By ',''),bg_hdr)
    ri=2
    tiers=[t for t in tier_order if t in df[tier_col].dropna().unique()]
    tiers+=[t for t in df[tier_col].dropna().unique() if t not in tiers]
    for t in tiers:
        sub=df[df[tier_col]==t]
        ri=write_dir_subrows(ws,ri,sub,t,C['alt'] if ri%2==0 else C['white'])

# ── By Def Rank bucket sheet (NEW) ────────────────────────────────────────────
def write_def_rank_bucket_sheet(wb, df):
    if 'def_rank' not in df.columns:
        return
    tmp = df.copy()
    tmp['def_rank_bucket'] = tmp['def_rank'].apply(_def_rank_bucket)

    ws = wb.create_sheet('By Def Rank')
    sheet_hdr8(ws, 'Def Rank Bucket', C['hdr5'])
    ri = 2
    for b in ['01-05','06-10','11-20','21-25','26-30','UNK']:
        sub = tmp[tmp['def_rank_bucket'] == b]
        if len(sub) == 0:
            continue
        ri = write_dir_subrows(ws, ri, sub, b, C['alt'] if ri % 2 == 0 else C['white'])

# ── Box Raw sheet ─────────────────────────────────────────────────────────────
def write_raw(wb,df):
    ws=wb.create_sheet('Box Raw')
    desired=['pp_projection_id','player','team','opp_team','prop_type_norm','pick_type','line',
             'bet_direction','tier','def_tier','minutes_tier','shot_role','usage_role',
             'edge','abs_edge','last5_hit_rate','last5_avg','season_avg',
             'last5_over','last5_under','projection','rank_score','ml_prob','ml_edge',
             'edge_score','blended_score',
             'actual','result','margin','void_reason_grade']
    cols=[c for c in desired if c in df.columns]
    widths={'pp_projection_id':14,'player':22,'team':6,'opp_team':6,'prop_type_norm':20,'pick_type':10,
            'line':7,'bet_direction':10,'tier':5,'def_tier':10,'minutes_tier':12,
            'shot_role':10,'usage_role':10,'edge':8,'abs_edge':8,
            'last5_hit_rate':13,'last5_avg':10,'season_avg':12,
            'last5_over':9,'last5_under':10,'projection':12,'rank_score':12,
            'ml_prob':10,'ml_edge':10,'edge_score':11,'blended_score':12,
            'actual':9,'result':8,'margin':8,'void_reason_grade':22}
    for ci,col in enumerate(cols,1):
        ws.column_dimensions[get_column_letter(ci)].width=widths.get(col,12)
        hc(ws,1,ci,col,bg=C['hdr'])
    ws.row_dimensions[1].height=20; ws.freeze_panes='A2'
    for ri,row in enumerate(df[cols].itertuples(),2):
        bg=C['alt'] if ri%2==0 else C['white']
        res=str(getattr(row,'result','')).upper()
        for ci,col in enumerate(cols,1):
            val=getattr(row,col,'')
            c_bg=res_bg(res) if col=='result' else (tier_bg(val) if col=='tier' else bg)
            cell=dc(ws,ri,ci,val,bg=c_bg,align='left' if col=='player' else 'center')
            if col=='result' and res in ('HIT','MISS','PUSH','VOID'):
                cell.font=Font(bold=True,name='Arial',size=9,color='FFFFFF')
    ws.auto_filter.ref=f"A1:{get_column_letter(len(cols))}1"

# ── Summary dashboard ─────────────────────────────────────────────────────────
def write_dashboard(wb,df,sport,date_str):
    ws=wb.create_sheet('Summary',0)
    ws.column_dimensions['A'].width=22
    ws.column_dimensions['B'].width=12
    for ci in range(3,10): ws.column_dimensions[get_column_letter(ci)].width=11
    ws.merge_cells('A1:I1')
    c=ws['A1']
    c.value=f"{sport} SLATE GRADE  |  {date_str}  |  Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    c.font=Font(bold=True,name='Arial',size=12,color='FFFFFF')
    c.fill=PatternFill('solid',start_color=C['hdr'])
    c.alignment=Alignment(horizontal='center',vertical='center')
    ws.row_dimensions[1].height=28
    d=drc(df)
    # Headline quality metrics exclude Tier D (keep Tier D rows in Box Raw / detail sheets).
    if 'tier' in df.columns:
        tier_u = df['tier'].astype(str).str.upper().str.strip()
        df_headline = df[tier_u.ne('D')].copy()
        headline_label = 'Full Slate (A+B+C)'
    else:
        df_headline = df
        headline_label = 'Full Slate'

    def sec_hdr(row,label,color):
        hc(ws,row,1,label,bg=color)
        for ci,h in enumerate(['Direction','Total','Decided','Hits','Misses','Voids','Hit Rate'],2):
            hc(ws,row,ci,h,bg=color)
        return row+1

    def simple_row(row,label,sub,bg=None,bold=True):
        hr2,h2,m2,v2,dec2=hit_rate(sub)
        bg=bg or (C['alt'] if row%2==0 else C['white'])
        dc(ws,row,1,label,bold=bold,align='left')
        dc(ws,row,2,'ALL',bg=bg,bold=True); dc(ws,row,3,len(sub),bg=bg)
        dc(ws,row,4,dec2,bg=bg); dc(ws,row,5,int(h2),bg=bg)
        dc(ws,row,6,int(m2),bg=bg); dc(ws,row,7,int(v2),bg=bg)
        pct_cell(ws,row,8,hr2); return row+1

    def dir_rows(row,sub):
        for direction in ['OVER','UNDER']:
            dsub=sub[sub[d].str.upper()==direction]
            if len(dsub)==0: continue
            hr_d,h_d,m_d,v_d,dec_d=hit_rate(dsub)
            dbg=C['over'] if direction=='OVER' else C['under']
            dc(ws,row,1,'',bg=dbg); dc(ws,row,2,direction,bg=dbg,bold=True)
            dc(ws,row,3,len(dsub),bg=dbg); dc(ws,row,4,dec_d,bg=dbg)
            dc(ws,row,5,int(h_d),bg=dbg); dc(ws,row,6,int(m_d),bg=dbg)
            dc(ws,row,7,int(v_d),bg=dbg); pct_cell(ws,row,8,hr_d); row+=1
        return row

    # OVERALL
    row=2
    hc(ws,row,1,'OVERALL',bg=C['hdr2'])
    for ci,h in enumerate(['Direction','Total Props','Decided','Hits','Misses','Voids','Hit Rate'],2):
        hc(ws,row,ci,h,bg=C['hdr2'])
    row+=1
    hr,hits,misses,voids,decided_n = hit_rate(df_headline)
    dc(ws,row,1,headline_label,bold=True,align='left'); dc(ws,row,2,'ALL')
    dc(ws,row,3,len(df_headline)); dc(ws,row,4,decided_n)
    dc(ws,row,5,int(hits)); dc(ws,row,6,int(misses)); dc(ws,row,7,int(voids))
    pct_cell(ws,row,8,hr); row+=1

    # BY PICK TYPE
    if 'pick_type' in df.columns:
        row+=1; row=sec_hdr(row,'BY PICK TYPE',C['hdr3'])
        for pt in ['Goblin','Demon','Standard']:
            sub=df[df['pick_type']==pt]
            if len(sub)==0: continue
            pt_bg={'Goblin':C['goblin'],'Demon':C['demon'],'Standard':C['standard']}.get(pt)
            row=simple_row(row,pt,sub,bg=pt_bg)
            if pt=='Standard': row=dir_rows(row,sub)

    # BY TIER
    if 'tier' in df.columns:
        row+=1; row=sec_hdr(row,'BY TIER',C['hdr4'])
        for t in TIER_ORDER:
            sub=df[df['tier'].astype(str).str.upper()==t]
            if len(sub)==0: continue
            row=simple_row(row,f'Tier {t}',sub,bg=tier_bg(t))
            row=dir_rows(row,sub)

    # BY DEF TIER
    if 'def_tier' in df.columns:
        row+=1; row=sec_hdr(row,'BY OPP DEF TIER',C['hdr5'])
        for dt in [t for t in DEF_TIER_ORDER if t in df['def_tier'].dropna().unique()]:
            sub=df[df['def_tier']==dt]
            row=simple_row(row,dt,sub); row=dir_rows(row,sub)

    # BY DEF RANK BUCKET (NEW)
    if 'def_rank' in df.columns:
        row+=1; row=sec_hdr(row,'BY OPP DEF RANK BUCKET',C['hdr5'])
        tmp = df.copy()
        tmp['def_rank_bucket'] = tmp['def_rank'].apply(_def_rank_bucket)
        for b in ['01-05','06-10','11-20','21-25','26-30','UNK']:
            sub = tmp[tmp['def_rank_bucket'] == b]
            if len(sub) == 0: 
                continue
            row = simple_row(row, b, sub)
            row = dir_rows(row, sub)

    # BY MINUTES TIER
    if 'minutes_tier' in df.columns:
        row+=1; row=sec_hdr(row,'BY MINUTES TIER',C['hdr6'])
        for mt in [t for t in MINUTES_TIER_ORDER if t in df['minutes_tier'].dropna().unique()]:
            sub=df[df['minutes_tier']==mt]
            row=simple_row(row,mt,sub); row=dir_rows(row,sub)

    # BY PLAYER ROLE
    if 'usage_role' in df.columns:
        row+=1; row=sec_hdr(row,'BY PLAYER ROLE',C['hdr7'])
        for role in [r for r in USAGE_ROLE_ORDER if r in df['usage_role'].dropna().unique()]:
            sub=df[df['usage_role']==role]
            row=simple_row(row,role,sub); row=dir_rows(row,sub)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--sport',default='NBA',choices=['NBA','CBB'])
    ap.add_argument('--slate',default='')
    ap.add_argument('--actuals',default='')
    ap.add_argument('--output',default='')
    ap.add_argument('--template',action='store_true')
    ap.add_argument('--date',default=datetime.now().strftime('%Y-%m-%d'))
    args=ap.parse_args()

    if args.template:
        pd.DataFrame(columns=['player','prop_type','actual']).to_csv(f'actuals_{args.sport.lower()}.csv',index=False)
        print(f'Saved actuals_{args.sport.lower()}.csv'); return

    if not args.slate: print('ERROR: --slate required.'); return
    if not args.output: args.output=f'{args.sport.lower()}_graded_{args.date}.xlsx'

    print(f'Loading {args.sport} slate...')
    df=load_nba(args.slate) if args.sport=='NBA' else load_cbb(args.slate)
    if args.sport == "NBA":
        df = filter_nba_slate_by_grade_date(df, args.date)
    print(f'  {len(df)} props loaded')

    if args.actuals:
        print(f'Applying actuals from {args.actuals}...')
        df=apply_actuals(df,args.actuals)
        headline_df = df
        if 'tier' in df.columns:
            tier_u = df['tier'].astype(str).str.upper().str.strip()
            headline_df = df[tier_u.ne('D')].copy()
        decided=headline_df[headline_df['result'].isin(['HIT','MISS'])]
        hits=(decided['result']=='HIT').sum()
        extra = " (A+B+C only)" if len(headline_df) != len(df) else ""
        print(f'  Graded{extra}: {len(decided)} decided — {hits} HIT / {len(decided)-hits} MISS')
    else:
        df['result']='PENDING'; df['void_reason_grade']=''; df['margin']=np.nan
        df['actual']=np.nan; df['result_sign']=0
        print('  No actuals — PENDING slate')

    if "pp_projection_id" not in df.columns and "projection_id" in df.columns:
        df["pp_projection_id"] = df["projection_id"]

    wb=Workbook(); wb.remove(wb.active)
    write_dashboard(wb,df,args.sport,args.date)
    write_raw(wb,df)
    write_pick_type_sheet(wb,df)
    write_tier_sheet(wb,df)
    write_prop_direction_sheet(wb,df)
    write_flat_breakdown(wb,breakdown(df,drc(df)),'By Direction',drc(df),C['hdr5'])
    if 'abs_edge_bucket' in df.columns:
        write_flat_breakdown(wb,breakdown(df,'abs_edge_bucket'),'By Edge Bucket','abs_edge_bucket',C['hdr'])
    write_tier_dir_sheet(wb,df,'By Minutes Tier','minutes_tier',MINUTES_TIER_ORDER,C['hdr6'])
    write_tier_dir_sheet(wb,df,'By Def Tier','def_tier',DEF_TIER_ORDER,C['hdr5'])
    write_def_rank_bucket_sheet(wb, df)  # NEW
    write_tier_dir_sheet(wb,df,'By Player Role','usage_role',USAGE_ROLE_ORDER,C['hdr7'])
    write_tier_dir_sheet(wb,df,'By Shot Role','shot_role',SHOT_ROLE_ORDER,C['hdr8'])

    vr_col='void_reason' if 'void_reason' in df.columns else 'void_reason_grade'
    vr_df=df[df[vr_col].notna()&(df[vr_col].astype(str).str.strip()!='')]
    if len(vr_df): write_flat_breakdown(wb,breakdown(vr_df,vr_col),'Void Reasons',vr_col,C['hdr'])

    wb.save(args.output)
    print(f'\nSaved -> {args.output}')
    print('Sheets:',wb.sheetnames)

if __name__=='__main__':
    main()