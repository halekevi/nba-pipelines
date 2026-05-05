"""
build_grades_html.py
====================
Reads  nba_graded_{date}.xlsx  and/or  cbb_graded_{date}.xlsx
produced by run_grader.ps1 and emits a styled slate_eval_{date}.html
into the same folder as this script (or wherever --out points).

Usage
-----
  # auto-detect yesterday's files:
  py -3.14 build_grades_html.py

  # explicit date:
  py -3.14 build_grades_html.py --date 2026-02-26

  # explicit file paths:
  py -3.14 build_grades_html.py --nba path\\to\\nba_graded_2026-02-26.xlsx
  py -3.14 build_grades_html.py --nba path\\nba.xlsx --cbb path\\cbb.xlsx

  # custom output location:
  py -3.14 build_grades_html.py --date 2026-02-26 --out ui_runner\\templates\\
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── openpyxl guard ────────────────────────────────────────────────────────────
try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl not installed.  Run:  pip install openpyxl")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
OUTPUTS_DIR = SCRIPT_DIR / "outputs"
ROOT_DIR    = SCRIPT_DIR.parent.parent  # scripts/grading -> scripts -> project root
# Two levels up from scripts/grading → project root → outputs/{date}
ROOT_DIR    = SCRIPT_DIR.parent.parent


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════════════════════════════════════

def h(v: Any) -> str:
    return html_lib.escape(str(v) if v is not None else "")

def pct(v: Any) -> str:
    """Return '55.2%' from 0.552 or 55.2 or '55.2%'."""
    if v is None: return "—"
    s = str(v).strip().rstrip("%")
    try:
        f = float(s)
        if f <= 1.0: f *= 100
        return f"{f:.1f}%"
    except (ValueError, TypeError):
        return str(v)

def pct_f(v: Any) -> float:
    """Return float 0-100."""
    if v is None: return 0.0
    s = str(v).strip().rstrip("%")
    try:
        f = float(s)
        return f * 100 if f <= 1.0 else f
    except (ValueError, TypeError):
        return 0.0

def rate_color(f: float) -> str:
    """CSS color variable for a hit-rate float 0-100."""
    if f >= 65: return "var(--green)"
    if f >= 58: return "#5ef598"
    if f >= 50: return "var(--gold)"
    return "var(--red)"

def rate_bar_html(f: float) -> str:
    col = rate_color(f)
    txt = "var(--red)" if f < 50 else ("var(--gold)" if f < 58 else "var(--green)")
    return (f'<div class="rate-cell">'
            f'<div class="rate-bar-bg"><div class="rate-bar-fill" style="width:{min(f,100):.1f}%;background:{col}"></div></div>'
            f'<div class="rate-num" style="color:{txt}">{f:.1f}%</div>'
            f'</div>')

def safe_int(v: Any) -> int:
    try: return int(float(str(v).replace(",","")))
    except: return 0

def fmt_num(n: int) -> str:
    return f"{n:,}"


# ══════════════════════════════════════════════════════════════════════════════
#  XLSX READING
# ══════════════════════════════════════════════════════════════════════════════

def read_sheet(ws) -> list[dict]:
    """Read first sheet with header row → list of dicts."""
    rows = list(ws.iter_rows(values_only=True))
    if not rows: return []
    headers = [str(c).strip() if c is not None else f"_c{i}" for i, c in enumerate(rows[0])]
    out = []
    for row in rows[1:]:
        if any(v is not None for v in row):
            out.append(dict(zip(headers, row)))
    return out


def _sheet_header_has_margin(ws) -> bool:
    row0 = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not row0:
        return False
    return any(str(c or "").strip().lower() == "margin" for c in row0)


def load_graded(path: Path) -> list[dict]:
    """
    Load graded workbook rows for Prop Evaluation / HTML.

    When **Box Raw** exists and includes a **margin** column (NBA/MLB slate_grader
    layout), load only that sheet so per-prop margin and actuals are present.
    Otherwise load every sheet (NHL/Soccer/simple workbooks).
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: list[dict] = []
    try:
        if "Box Raw" in wb.sheetnames and _sheet_header_has_margin(wb["Box Raw"]):
            br_rows = read_sheet(wb["Box Raw"])
            # Some dates have a margin-ready Box Raw header but no per-prop rows yet; fall back.
            if len(br_rows) > 0:
                rows = br_rows
            else:
                for shname in wb.sheetnames:
                    rows.extend(read_sheet(wb[shname]))
        else:
            for shname in wb.sheetnames:
                rows.extend(read_sheet(wb[shname]))
    finally:
        wb.close()
    # Normalize common column name variants
    normalized = []
    for r in rows:
        nr: dict[str, Any] = {}
        for k, v in r.items():
            nk = k.strip()
            # common aliases
            nk = re.sub(r"pick[\s_-]*type", "Pick Type", nk, flags=re.I)
            nk = re.sub(r"\bhit[\s_]rate\b", "Hit Rate", nk, flags=re.I)
            nk = re.sub(r"\bdef[\s_]?tier\b", "Def Tier", nk, flags=re.I)
            nk = re.sub(r"\bprop[\s_]type\b", "Prop Type", nk, flags=re.I)
            nk = re.sub(r"\bmin(utes)?\b", "Minutes", nk, flags=re.I)
            # Normalize result/grade column
            if nk.lower() == "result":              nk = "Result"
            if nk.lower() == "grade":               nk = "Result"
            if nk.lower() == "void_reason_grade":   nk = "void_reason"
            if nk.lower() == "bet_direction":       nk = "Direction"
            if nk.lower() == "prop_type_norm":      nk = "Prop Type"
            if nk.lower() == "pick_type":           nk = "Pick Type"
            if nk.lower() == "tier":                nk = "Tier"
            if nk.lower() == "pick_type":           nk = "Pick Type"
            nr[nk] = v
        normalized.append(nr)
    return normalized



# ══════════════════════════════════════════════════════════════════════════════
#  GRADED PROPS JSON (Grades UI /api/graded-props)
# ══════════════════════════════════════════════════════════════════════════════

def _cell_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("none", "nan", ""):
        return ""
    return s


def _norm_result_display(row: dict) -> str:
    r = _cell_str(row.get("Result") or row.get("Grade")).upper()
    if r in ("HIT", "WIN", "1", "TRUE", "YES", "W"):
        return "HIT"
    if r in ("MISS", "LOSS", "0", "FALSE", "NO", "L"):
        return "MISS"
    if r in ("VOID", "PUSH", "N/A"):
        return r
    if r:
        return r
    return "—"


def prop_row_for_api(row: dict, sport: str) -> dict[str, str] | None:
    """One flat dict per prop row for the Prop Evaluation tab."""
    def _pick(*keys: str) -> str:
        for k in keys:
            v = _cell_str(row.get(k))
            if v:
                return v
        return ""

    player = _pick("Player", "player", "Name", "name")
    if not player:
        return None
    team = _pick("Team", "team", "team_abbr")
    prop = _pick("Prop Type", "prop type", "Prop", "prop", "Pick", "pick")
    direction = _pick("Dir", "dir", "Direction", "direction").upper()
    line = _pick("Line", "line", "Game Line", "game line", "O/U", "OU Line", "Pick Line")
    pick_type = _pick("Pick Type", "pick type")
    tier = _pick("Tier", "tier")
    # slate_grader Box Raw uses snake_case opp_team; legacy sheets use Opp / Opp Team.
    opp_team = _pick(
        "opp_team",
        "Opp Team",
        "opp team",
        "Opp",
        "opp",
        "Opponent",
        "opponent",
        "opp_team_abbr",
        "pp_opp_team",
    )
    edge = _pick("Edge", "edge", "Edge Score", "edge score")
    ml_prob = _pick("ML Prob", "ml prob", "ml_prob")
    actual_value = _pick("Actual", "actual", "actual_value")
    margin = _pick("Margin", "margin")
    def_tier = _pick("Def Tier", "def_tier", "Defense Tier", "defense_tier", "Opp Def Tier", "opp_def_tier")
    h2h_bucket = _pick("H2H Tier", "h2h_tier", "H2H Bucket", "h2h_bucket", "Head To Head Bucket", "head_to_head_bucket")
    minutes_tier = _pick("Minutes Tier", "minutes_tier", "Min Tier", "min_tier", "Minutes Bucket", "minutes_bucket")
    role_tier = _pick("Role Tier", "role_tier", "Player Role", "player_role", "Usage Role", "usage_role", "Team Role", "team_role")
    game_total_bucket = _pick("Game Total Bucket", "game_total_bucket", "OU Bucket", "ou_bucket", "Over Under Bucket", "over_under_bucket", "Total Bucket", "total_bucket")
    game_total = _pick("Game O/U", "game_ou", "Game Total", "game_total", "O/U Total", "ou_total")
    h2h_raw = _pick("H2H", "h2h", "Head To Head", "head_to_head")
    actual_source = _pick("actual_source", "Actual Source", "actual source")
    actual_source_conflict = _pick("actual_source_conflict", "Actual Source Conflict", "actual source conflict")
    void_reason = _pick(
        "void_reason",
        "Void Reason",
        "void reason",
        "reason",
        "Reason",
        "status_note",
        "notes",
    )
    proj_id = _pick("pp_projection_id", "projection_id", "PP Projection ID", "Projection ID")
    result = _norm_result_display(row)
    sport_up = str(sport or "").strip().upper()
    vr_up = str(void_reason or "").strip().upper()
    # Soccer VOID rows must carry explicit data quality tags for validator/reporting.
    if result == "VOID" and sport_up == "SOCCER":
        if "DNP" in vr_up:
            void_reason = "DNP"
        elif "NO_DATA" in vr_up or "NO ACTUAL" in vr_up:
            void_reason = "NO_DATA"
        elif not vr_up:
            void_reason = "NO_DATA"
    return {
        "sport": sport,
        "player": player,
        "team": team or "—",
        "opp_team": opp_team or "",
        "prop": prop or "—",
        "line": line or "—",
        "direction": direction or "—",
        "pick_type": pick_type or "—",
        "tier": tier or "",
        "edge": edge or "",
        "ml_prob": ml_prob or "",
        "actual_value": actual_value or "",
        "margin": margin or "",
        "def_tier": def_tier or "",
        "h2h_bucket": h2h_bucket or "",
        "minutes_tier": minutes_tier or "",
        "role_tier": role_tier or "",
        "game_total_bucket": game_total_bucket or "",
        "game_total": game_total or "",
        "h2h_raw": h2h_raw or "",
        "actual_source": actual_source or "",
        "actual_source_conflict": actual_source_conflict or "",
        "over_under": direction or "—",
        "void_reason": void_reason or "",
        "result": result,
        "pp_projection_id": proj_id or "",
    }


def export_graded_props_json(
    date_str: str,
    out_dir: Path,
    bundles: list[tuple[str, list[dict]]],
) -> Path:
    props: list[dict[str, str]] = []
    for sport, rows in bundles:
        for row in rows:
            p = prop_row_for_api(row, sport)
            if p:
                props.append(p)
    payload = {"date": date_str, "count": len(props), "props": props}
    out = out_dir / f"graded_props_{date_str}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

def agg_rows(rows: list[dict], key_col: str) -> dict[str, dict]:
    """Group rows by key_col and sum Hits, Misses, Decided, Voids."""
    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"hits":0,"misses":0,"decided":0,"voids":0})
    for r in rows:
        k = str(r.get(key_col, "") or "").strip()
        if not k or k.lower() in ("none","nan",""): continue
        result = str(r.get("Result","") or r.get("Grade","") or "").strip().upper()
        void   = result in ("VOID","PUSH","N/A","")
        if void:
            groups[k]["voids"] += 1
        else:
            groups[k]["decided"] += 1
            if result in ("HIT","WIN","1","TRUE","YES","W"):
                groups[k]["hits"] += 1
            elif result in ("MISS","LOSS","0","FALSE","NO","L"):
                groups[k]["misses"] += 1
            else:
                # Try Hit Rate column directly per row if no result col
                hr = r.get("Hit Rate")
                if hr is not None:
                    try:
                        f = float(str(hr).rstrip("%"))
                        if f > 1: f /= 100
                        groups[k]["hits"]   += round(f)
                        groups[k]["misses"] += 1 - round(f)
                    except: pass
    return dict(groups)


def agg_by_precomputed(rows: list[dict], key_col: str) -> list[dict]:
    """
    When rows already have Decided / Hits / Misses columns (pre-aggregated),
    just group and sum them.
    """
    groups: dict[str, dict] = defaultdict(lambda: {"hits":0,"misses":0,"decided":0,"voids":0})
    for r in rows:
        k = str(r.get(key_col, "") or "").strip()
        if not k or k.lower() in ("none","nan",""): continue
        groups[k]["hits"]    += safe_int(r.get("Hits",   r.get("hits",   0)))
        groups[k]["misses"]  += safe_int(r.get("Misses", r.get("misses", 0)))
        groups[k]["decided"] += safe_int(r.get("Decided",r.get("decided",0)))
        groups[k]["voids"]   += safe_int(r.get("Voids",  r.get("voids",  0)))
    result = []
    for k, v in groups.items():
        d = v["decided"]
        h_ = v["hits"]
        result.append({
            "key": k,
            "hits": h_,
            "misses": v["misses"],
            "decided": d,
            "voids": v["voids"],
            "hit_rate": (h_/d*100) if d > 0 else 0.0
        })
    return result


def build_agg_from_rows(rows: list[dict], key_col: str) -> list[dict]:
    """
    Detect whether rows are pre-aggregated (have Decided/Hits cols) or
    raw (have a Result col). Returns list of aggregated dicts with hit_rate.
    """
    has_decided = any(r.get("Decided") is not None or r.get("decided") is not None for r in rows[:20])
    if has_decided:
        return agg_by_precomputed(rows, key_col)
    else:
        raw = agg_rows(rows, key_col)
        result = []
        for k, v in raw.items():
            d = v["decided"]
            h_ = v["hits"]
            result.append({
                "key": k,
                "hits": h_,
                "misses": v["misses"],
                "decided": d,
                "voids": v["voids"],
                "hit_rate": (h_/d*100) if d > 0 else 0.0
            })
        return result


def overall_stats(rows: list[dict]) -> dict:
    """Compute overall summary from graded rows."""
    total = len(rows)
    agg = build_agg_from_rows(rows, "_all_")  # dummy — we compute directly
    # Direct computation
    hits = misses = voids = 0
    for r in rows:
        result = str(r.get("Result","") or r.get("Grade","") or "").strip().upper()
        if result in ("VOID","PUSH","N/A",""): voids += 1
        elif result in ("HIT","WIN","1","TRUE","YES","W"): hits += 1
        elif result in ("MISS","LOSS","0","FALSE","NO","L"): misses += 1
    decided = hits + misses
    # Fallback: pre-aggregated sheet (rows already have Decided/Hits)
    if decided == 0 and total > 0:
        has_d = any(r.get("Decided") is not None for r in rows[:10])
        if has_d:
            hits    = sum(safe_int(r.get("Hits",   0)) for r in rows)
            misses  = sum(safe_int(r.get("Misses", 0)) for r in rows)
            decided = sum(safe_int(r.get("Decided",0)) for r in rows)
            voids   = sum(safe_int(r.get("Voids",  0)) for r in rows)
            total   = decided + voids
    hit_rate = (hits/decided*100) if decided > 0 else 0.0
    return {"total":total,"hits":hits,"misses":misses,"decided":decided,"voids":voids,"hit_rate":hit_rate}


def tier_a_stats(rows: list[dict]) -> dict:
    tier_rows = [r for r in rows if str(r.get("Tier","") or "").strip().upper() == "A"]
    return overall_stats(tier_rows)

def pick_type_stats(rows: list[dict], pick_type: str) -> dict:
    pt = pick_type.lower()
    filtered = [r for r in rows if pt in str(r.get("Pick Type","") or "").lower()]
    return overall_stats(filtered)


def rows_for_pick_type(sub_rows: list[dict], pick_type: str) -> list[dict]:
    pt = pick_type.lower()
    return [r for r in sub_rows if pt in str(r.get("Pick Type", "") or "").lower()]


def over_under_lines_html(sub_rows: list[dict]) -> str:
    """▲ OVER / ▼ UNDER hit rates for any row subset (pick type bucket, tier bucket, def tier slice, etc.)."""
    over = overall_stats(
        [r for r in sub_rows if str(r.get("Dir", "") or r.get("Direction", "")).strip().upper() == "OVER"]
    )
    under = overall_stats(
        [r for r in sub_rows if str(r.get("Dir", "") or r.get("Direction", "")).strip().upper() == "UNDER"]
    )
    inner = ""
    if over["decided"] > 0:
        col = "var(--green)" if over["hit_rate"] >= 55 else "var(--red)"
        inner += (
            f'<div class="sub-dir"><span style="color:var(--cyan);font-size:13px">▲ OVER</span> '
            f'<span style="color:{col}">{pct(over["hit_rate"])}</span> ({fmt_num(over["decided"])} dec)</div>'
        )
    if under["decided"] > 0:
        col = "var(--green)" if under["hit_rate"] >= 55 else "var(--red)"
        inner += (
            f'<div class="sub-dir"><span style="color:var(--gold);font-size:13px">▼ UNDER</span> '
            f'<span style="color:{col}">{pct(under["hit_rate"])}</span> ({fmt_num(under["decided"])} dec)</div>'
        )
    if not inner:
        return ""
    return (
        f'<div class="ou-breakdown" style="font-size:13px;color:var(--muted2);margin-top:5px;line-height:1.45">{inner}</div>'
    )


def _norm_pick_type_matrix(v: object) -> str | None:
    s = str(v or "").strip().lower()
    if "goblin" in s:
        return "Goblin"
    if "demon" in s:
        return "Demon"
    if "standard" in s:
        return "Standard"
    return None


def normalize_def_tier_label(raw: object) -> str | None:
    """
    Map a Def Tier cell to canonical buckets: Elite, Above Avg, Avg, Below Avg, Weak.
    Returns None if the value cannot be mapped (row excluded from tier rows, still eligible for Total).
    """
    s = (
        str(raw or "")
        .lower()
        .replace("🟢", "")
        .replace("🟡", "")
        .replace("🔴", "")
        .strip()
    )
    if not s:
        return None
    if "below" in s and "avg" in s:
        return "Below Avg"
    if "above" in s and "avg" in s:
        return "Above Avg"
    if "elite" in s:
        return "Elite"
    if "weak" in s:
        return "Weak"
    if s in ("avg", "average"):
        return "Avg"
    return None


def normalize_bet_direction(raw: object) -> str | None:
    t = str(raw or "").strip().upper()
    for ch in ("\u25b2", "\u25bc", "\u2191", "\u2193", "▲", "▼"):
        t = t.replace(ch, "")
    t = re.sub(r"\s+", " ", t).strip()
    if "UNDER" in t:
        return "UNDER"
    if "OVER" in t:
        return "OVER"
    return None


def _rows_for_def_subgrid_cell(
    rows: list[dict],
    canon_def: str | None,
    pick: str,
    direction: str,
    rank: str,
) -> list[dict]:
    """Filter props for one COMBINED subgrid cell. canon_def None = no def-tier filter (Total row)."""
    out: list[dict] = []
    for r in rows:
        if canon_def is not None:
            if normalize_def_tier_label(r.get("Def Tier")) != canon_def:
                continue
        pt = _norm_pick_type_matrix(r.get("Pick Type"))
        if pt != pick:
            continue
        bd = normalize_bet_direction(r.get("Dir") or r.get("Direction"))
        if bd != direction:
            continue
        rk = str(r.get("Tier", "") or "").strip().upper()
        if rk != rank:
            continue
        out.append(r)
    return out


def _subgrid_hit_color_hex(hit_rate: float) -> str:
    if hit_rate >= 65.0:
        return "#1D9E75"
    if hit_rate >= 50.0:
        return "#BA7517"
    return "#A32D2D"


def _def_tier_combined_subgrid_table(rows: list[dict], min_decided: int = 10) -> tuple[str, int]:
    """
    DEF TIER BREAKDOWN — pick type × rank tier (16 data cols + label col).
    Rows: five canonical def tiers + Total. Two-row header.
    Returns (html_fragment, count of cells with decided >= min_decided among 5×16 tier cells).
    """
    canon_defs = ("Elite", "Above Avg", "Avg", "Below Avg", "Weak")
    groups: tuple[tuple[str, str, str], ...] = (
        ("Goblin OVER", "Goblin", "OVER"),
        ("Demon OVER", "Demon", "OVER"),
        ("Std OVER", "Standard", "OVER"),
        ("Std UNDER", "Standard", "UNDER"),
    )
    ranks = ("A", "B", "C", "D")

    mute = "var(--color-text-tertiary, var(--muted2))"
    br_sec = "var(--color-border-secondary, rgba(255,255,255,0.14))"
    br_ter = "var(--color-border-tertiary, rgba(255,255,255,0.08))"
    bg_sec = "var(--color-background-secondary, rgba(255,255,255,0.04))"

    def _cell_td(stats: dict, col_idx: int) -> str:
        """col_idx 1..16 — determines group boundary borders."""
        within = (col_idx - 1) % 4
        bl = f"border-left:1.5px solid {br_sec}" if within == 0 else f"border-left:0.5px solid {br_ter}"
        base = (
            f'padding:6px 8px;text-align:center;font-size:12px;vertical-align:middle;{bl}'
        )
        d = int(stats.get("decided", 0) or 0)
        if d <= 0:
            return f'<td style="{base};color:{mute}">—</td>'
        hr = float(stats.get("hit_rate", 0.0))
        col = _subgrid_hit_color_hex(hr)
        pct_s = f"{hr:.0f}%"
        return (
            f'<td style="{base}">'
            f'<span style="color:{col};font-weight:700">{pct_s}</span>'
            f'<span style="font-size:10px;color:{mute};display:block;margin-top:1px">({fmt_num(d)})</span>'
            f"</td>"
        )

    n_ge = 0
    body_rows = ""
    for def_label in canon_defs:
        row_cells = ""
        col_idx = 1
        for _gh, pick, direction in groups:
            for rank in ranks:
                cell_rows = _rows_for_def_subgrid_cell(rows, def_label, pick, direction, rank)
                st = overall_stats(cell_rows)
                if int(st.get("decided", 0)) >= int(min_decided):
                    n_ge += 1
                row_cells += _cell_td(st, col_idx)
                col_idx += 1
        body_rows += (
            f'<tr class="def-subgrid-data-row">'
            f'<td style="padding:6px 8px;text-align:left;font-weight:500;font-size:12px;border-left:none">{def_label}</td>'
            f"{row_cells}</tr>"
        )

    total_cells = ""
    tcol_idx = 1
    for _gh, pick, direction in groups:
        for rank in ranks:
            cell_rows = _rows_for_def_subgrid_cell(rows, None, pick, direction, rank)
            st = overall_stats(cell_rows)
            total_cells += _cell_td(st, tcol_idx)
            tcol_idx += 1

    body_rows += (
        f'<tr style="background:{bg_sec};font-weight:500">'
        f'<td style="padding:6px 8px;text-align:left;font-size:12px;font-weight:500;border-left:none">Total</td>'
        f"{total_cells}</tr>"
    )

    hdr2 = ""
    for gi, (_glabel, _p, _d) in enumerate(groups):
        for ri, rank in enumerate(ranks):
            col_idx = gi * 4 + ri + 1
            group_idx = (col_idx - 1) // 4
            within = (col_idx - 1) % 4
            bl = f"border-left:1.5px solid {br_sec}" if within == 0 else f"border-left:0.5px solid {br_ter}"
            hdr2 += (
                f'<th style="padding:4px 6px;font-size:10px;font-weight:500;color:{mute};{bl}">{rank}</th>'
            )

    hdr1_cells = ""
    for glabel, _p, _d in groups:
        hdr1_cells += (
            f'<th colspan="4" style="padding:6px 8px;font-size:11px;color:var(--color-text-secondary, var(--muted));'
            f"text-align:center;font-weight:500;border-left:1.5px solid {br_sec}\">{h(glabel)}</th>"
        )

    table_html = f"""<style>
.def-tier-subgrid-table tbody tr.def-subgrid-data-row:hover td {{ background:{bg_sec}; }}
</style>
<div class="def-tier-combined-wrap" style="overflow-x:auto;padding:0.5rem 0">
<table class="def-tier-subgrid-table" style="min-width:920px;border-collapse:collapse;font-size:12px;width:100%">
  <thead>
    <tr>
      <th rowspan="2" style="padding:6px 8px;text-align:left;font-size:11px;font-weight:600;border-left:none;vertical-align:bottom">Def Tier</th>
      {hdr1_cells}
    </tr>
    <tr>
      {hdr2}
    </tr>
  </thead>
  <tbody>
    {body_rows}
  </tbody>
</table>
</div>"""
    return table_html, n_ge


def build_pick_tier_direction_agg(rows: list[dict]) -> dict[tuple[str, str, str], dict[str, int]]:
    """
    Accumulate decided / hits / misses per (pick type, tier A-D, direction).
    Goblin/Demon: OVER only; Standard: OVER + UNDER.
    """
    agg: dict[tuple[str, str, str], dict[str, int]] = {}
    for r in rows:
        pt = _norm_pick_type_matrix(r.get("Pick Type"))
        if not pt:
            continue
        tier = str(r.get("Tier", "") or "").strip().upper()
        if tier not in ("A", "B", "C", "D"):
            continue
        direction = str(r.get("Dir", "") or r.get("Direction", "")).strip().upper()
        if direction not in ("OVER", "UNDER"):
            continue
        if pt in ("Goblin", "Demon") and direction != "OVER":
            continue

        key = (pt, tier, direction)
        if key not in agg:
            agg[key] = {"hits": 0, "misses": 0, "decided": 0}

        result = str(r.get("Result", "") or r.get("Grade", "") or "").strip().upper()
        if result in ("HIT", "WIN", "1", "TRUE", "YES", "W"):
            agg[key]["hits"] += 1
            agg[key]["decided"] += 1
        elif result in ("MISS", "LOSS", "0", "FALSE", "NO", "L"):
            agg[key]["misses"] += 1
            agg[key]["decided"] += 1
        else:
            d = safe_int(r.get("Decided", r.get("decided", 0)))
            h_ = safe_int(r.get("Hits", r.get("hits", 0)))
            m_ = safe_int(r.get("Misses", r.get("misses", 0)))
            if d > 0 or h_ > 0 or m_ > 0:
                agg[key]["decided"] += d
                agg[key]["hits"] += h_
                agg[key]["misses"] += m_
    return agg


def _canonical_pick_tier_direction_keys() -> list[tuple[str, str, str]]:
    """Fixed display order: Standard (A-D × O/U), Goblin (A-D OVER), Demon (A-D OVER)."""
    keys: list[tuple[str, str, str]] = []
    for tier in ("A", "B", "C", "D"):
        keys.append(("Standard", tier, "OVER"))
        keys.append(("Standard", tier, "UNDER"))
    for tier in ("A", "B", "C", "D"):
        keys.append(("Goblin", tier, "OVER"))
    for tier in ("A", "B", "C", "D"):
        keys.append(("Demon", tier, "OVER"))
    return keys


def pick_tier_direction_matrix_html(rows: list[dict], min_decided: int = 10) -> str:
    agg = build_pick_tier_direction_agg(rows)
    keys = _canonical_pick_tier_direction_keys()
    n_ge = sum(1 for k in keys if int(agg.get(k, {}).get("decided", 0)) >= int(min_decided))
    summary = (
        f"Full grid: Standard A–D (OVER/UNDER), Goblin & Demon A–D (OVER). "
        f"{n_ge} of {len(keys)} cells have ≥ {int(min_decided)} decided; others still listed with counts. "
        f"Note: Standard Tier B may show no decided props when ml_prob is compressed below tier thresholds "
        f"(e.g., B cut ≥ 0.65) — “—” cells are expected until calibration / a retrain restores spread."
    )

    body_rows = ""
    for pt, tier, direction in keys:
        v = agg.get((pt, tier, direction), {"hits": 0, "misses": 0, "decided": 0})
        d = int(v["decided"])
        h_ = int(v["hits"])
        m_ = int(v["misses"])
        pt_chip = {
            "Goblin": '<span class="chip chip-goblin">🎃\u00a0Goblin</span>',
            "Demon": '<span class="chip chip-demon">😈\u00a0Demon</span>',
            "Standard": '<span class="chip chip-std">⭐\u00a0Standard</span>',
        }.get(pt, h(pt))
        tier_u = str(tier).upper()
        tier_chip_cls = {"A": "chip-a", "B": "chip-b", "C": "chip-c"}.get(tier_u, "chip-d")
        dir_html = (
            '<span style="color:var(--green);font-size:13px">▲ OVER</span>'
            if direction == "OVER"
            else '<span style="color:var(--cyan);font-size:13px">▼ UNDER</span>'
        )
        if d <= 0:
            body_rows += f"""<tr class="matrix-empty">
          <td>{pt_chip}</td>
          <td><span class="chip {tier_chip_cls}">TIER\u00a0{tier_u}</span></td>
          <td>{dir_html}</td>
          <td class="right mono muted">—</td>
          <td class="right mono muted">—</td>
          <td class="right mono muted">—</td>
          <td class="mono right muted">—</td>
          <td><span class="muted">—</span></td>
        </tr>"""
            continue
        hr = (h_ / d * 100.0) if d > 0 else 0.0
        row_cls = "matrix-hit" if hr >= 60.0 else ("matrix-miss" if hr < 50.0 else "matrix-warn")
        if d < int(min_decided):
            row_cls += " matrix-sparse"
        bar_color = "var(--green)" if hr >= 60.0 else ("var(--gold)" if hr >= 50.0 else "var(--red)")
        bar_html = (
            f'<div class="rate-bar-bg"><div class="rate-bar-fill" '
            f'style="width:{max(0.0, min(hr, 100.0)):.1f}%;background:{bar_color}"></div></div>'
        )
        body_rows += f"""<tr class="{row_cls}">
          <td>{pt_chip}</td>
          <td><span class="chip {tier_chip_cls}">TIER\u00a0{tier_u}</span></td>
          <td>{dir_html}</td>
          <td class="right mono">{fmt_num(d)}</td>
          <td class="right mono pos">{fmt_num(h_)}</td>
          <td class="right mono neg">{fmt_num(m_)}</td>
          <td class="mono right">{pct(hr)}</td>
          <td>{bar_html}</td>
        </tr>"""

    return f"""<details class="matrix-collapsible">
      <summary>Performance Matrix — Pick Type × Tier × Direction</summary>
      <div class="matrix-body">
        <div class="matrix-summary">{summary}</div>
        <div class="table-wrap"><table class="table-sortable">
          <thead><tr>
            <th data-sort-key="pick" title="Sort">PICK TYPE</th>
            <th data-sort-key="tier" title="Sort">TIER</th>
            <th data-sort-key="dir" title="Sort">DIRECTION</th>
            <th class="right" data-sort-key="decided" title="Sort">DECIDED</th>
            <th class="right" data-sort-key="hits" title="Sort">HITS</th>
            <th class="right" data-sort-key="misses" title="Sort">MISSES</th>
            <th data-sort-key="rate" title="Sort">HIT RATE</th>
            <th data-sort-key="bar" title="Sort">BAR</th>
          </tr></thead>
          <tbody>{body_rows}</tbody>
        </table></div>
      </div>
    </details>"""


def pick_type_tier_direction_split_html(rows: list[dict], min_decided: int = 10) -> str:
    """
    User-facing split used for every sport section:
      - Goblin by Tier A-D (OVER only)
      - Demon by Tier A-D (OVER only)
      - Standard by Tier A-D (OVER + UNDER)
    """
    agg = build_pick_tier_direction_agg(rows)
    grid = _canonical_pick_tier_direction_keys()
    n_ge = sum(1 for k in grid if int(agg.get(k, {}).get("decided", 0)) >= int(min_decided))

    def _rec_for(pt: str, tier: str, direction: str) -> dict | None:
        v = agg.get((pt, tier, direction), {"decided": 0, "hits": 0, "misses": 0})
        d = int(v["decided"])
        if d <= 0:
            return None
        h_ = int(v["hits"])
        m_ = int(v["misses"])
        hr = (h_ / d * 100.0) if d > 0 else 0.0
        return {"decided": d, "hits": h_, "misses": m_, "hit_rate": hr}

    def _row_block(pt: str, direction: str, tier: str, rec: dict | None) -> str:
        if rec is None:
            return (
                f'<tr class="matrix-empty"><td><span class="chip chip-std">TIER {tier}</span></td>'
                f'<td>{"▲ OVER" if direction=="OVER" else "▼ UNDER"}</td>'
                f'<td class="right mono muted">—</td><td class="right mono muted">—</td>'
                f'<td class="right mono muted">—</td><td class="right mono muted">—</td>'
                f'<td><span class="muted">—</span></td></tr>'
            )
        hr = float(rec["hit_rate"])
        row_cls = "matrix-hit" if hr >= 60.0 else ("matrix-miss" if hr < 50.0 else "matrix-warn")
        if int(rec["decided"]) < int(min_decided):
            row_cls += " matrix-sparse"
        dir_html = (
            '<span style="color:var(--green);font-size:13px">▲ OVER</span>'
            if direction == "OVER"
            else '<span style="color:var(--cyan);font-size:13px">▼ UNDER</span>'
        )
        bar_color = "var(--green)" if hr >= 60.0 else ("var(--gold)" if hr >= 50.0 else "var(--red)")
        bar_html = (
            f'<div class="rate-bar-bg"><div class="rate-bar-fill" '
            f'style="width:{max(0.0, min(hr, 100.0)):.1f}%;background:{bar_color}"></div></div>'
        )
        chip_cls = {"A": "chip-a", "B": "chip-b", "C": "chip-c"}.get(tier, "chip-d")
        return f"""<tr class="{row_cls}">
          <td><span class="chip {chip_cls}">TIER {tier}</span></td>
          <td>{dir_html}</td>
          <td class="right mono">{fmt_num(rec['decided'])}</td>
          <td class="right mono pos">{fmt_num(rec['hits'])}</td>
          <td class="right mono neg">{fmt_num(rec['misses'])}</td>
          <td class="right mono">{pct(rec['hit_rate'])}</td>
          <td>{bar_html}</td>
        </tr>"""

    def _table_for(pt: str, dirs: list[str], chip_cls: str, chip_emoji: str) -> str:
        body = ""
        for t in ("A", "B", "C", "D"):
            for d in dirs:
                body += _row_block(pt, d, t, _rec_for(pt, t, d))
        return f"""<div>
          <div class="section-label"><span class="chip {chip_cls}">{chip_emoji}&nbsp;{pt}</span> BY TIER</div>
          <div class="table-wrap"><table class="table-sortable">
            <thead><tr>
              <th data-sort-key="tier" title="Sort">TIER</th>
              <th data-sort-key="dir" title="Sort">DIRECTION</th>
              <th class="right" data-sort-key="decided" title="Sort">DECIDED</th>
              <th class="right" data-sort-key="hits" title="Sort">HITS</th>
              <th class="right" data-sort-key="misses" title="Sort">MISSES</th>
              <th class="right" data-sort-key="rate" title="Sort">HIT RATE</th>
              <th data-sort-key="bar" title="Sort">BAR</th>
            </tr></thead>
            <tbody>{body}</tbody>
          </table></div>
        </div>"""

    return f"""<details class="matrix-collapsible">
      <summary>Pick Type Tier Splits — Goblin/Demon OVER, Standard OVER+UNDER</summary>
      <div class="matrix-body">
        <div class="matrix-summary">Full A–D per pick type ({len(grid)} matrix cells). {n_ge} cells with ≥ {int(min_decided)} decided. Note: Standard Tier B may be structurally sparse when ml_prob is compressed below tier thresholds — “—” is not a missing-data bug.</div>
        <div class="three-col">
          {_table_for("Goblin", ["OVER"], "chip-goblin", "🎃")}
          {_table_for("Demon", ["OVER"], "chip-demon", "😈")}
          {_table_for("Standard", ["OVER", "UNDER"], "chip-std", "⭐")}
        </div>
      </div>
    </details>"""


# ══════════════════════════════════════════════════════════════════════════════
#  HTML FRAGMENT BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def pick_type_row(label: str, icon: str, agg: dict, extra_html: str = "") -> str:
    d   = agg["decided"]
    h_  = agg["hits"]
    m   = agg["misses"]
    hr  = agg["hit_rate"]
    return f"""<tr>
      <td><span class="chip chip-{label.lower()}">{icon}\u00a0{label}</span>{extra_html}</td>
      <td class="right mono">{fmt_num(d)}</td>
      <td class="right mono pos">{fmt_num(h_)}</td>
      <td class="right mono neg">{fmt_num(m)}</td>
      <td>{rate_bar_html(hr)}</td>
    </tr>"""


def tier_row(tier: str, agg: dict, extra: str = "") -> str:
    d   = agg["decided"]
    h_  = agg["hits"]
    hr  = agg["hit_rate"]
    row_cls = "player-hit" if hr >= 55 else ("player-miss" if hr < 48 else "player-warn")
    chip_cls = {"A":"chip-a","B":"chip-b","C":"chip-c"}.get(tier.upper(), "chip-d")
    over_pct = pct(agg.get("over_rate", None))
    under_pct = pct(agg.get("under_rate", None))
    dir_html = ""
    if over_pct != "—":
        col = "var(--green)" if pct_f(over_pct) >= 55 else "var(--red)"
        dir_html += f'<span style="color:var(--cyan);font-size:12px;margin-right:8px">▲ </span><span style="color:{col};font-size:12px;margin-right:8px">{over_pct}</span>'
    if under_pct != "—":
        col = "var(--green)" if pct_f(under_pct) >= 55 else "var(--red)"
        dir_html += f'<span style="color:var(--gold);font-size:12px">▼ </span><span style="color:{col};font-size:12px">{under_pct}</span>'
    tier_lbl = f"TIER {tier.upper()}"
    return f"""<tr class="{row_cls}">
      <td><span class="chip {chip_cls}">{tier_lbl}</span>
      <div style="font-size:12px;color:var(--muted2);margin-top:4px">{dir_html}{extra}</div></td>
      <td class="right mono">{fmt_num(d)}</td>
      <td class="right mono pos">{fmt_num(h_)}</td>
      <td>{rate_bar_html(hr)}</td>
    </tr>"""


def prop_type_table(data: list[dict], label: str, min_decided: int = 10) -> str:
    filtered = [d for d in data if d["decided"] >= min_decided]
    filtered.sort(key=lambda x: x["hit_rate"], reverse=True)
    if not filtered:
        return f'<div class="muted-note">No prop types with ≥{min_decided} decided.</div>'
    rows_html = ""
    for d in filtered:
        row_cls = "player-hit" if d["hit_rate"] >= 55 else ("player-miss" if d["hit_rate"] < 48 else "player-warn")
        rows_html += f"""<tr class="{row_cls}">
          <td class="mono">{h(d['key'])}</td>
          <td class="right mono">{fmt_num(d['decided'])}</td>
          <td class="right mono pos">{fmt_num(d['hits'])}</td>
          <td>{rate_bar_html(d['hit_rate'])}</td>
        </tr>"""
    return f"""<div class="table-wrap"><table>
      <thead><tr><th>{label}</th><th class="right">DECIDED</th><th class="right">HITS</th><th>HIT RATE</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>"""


def player_table(rows: list[dict], top: bool, min_decided: int = 5, limit: int = 8) -> str:
    """Build top/worst player+prop+direction consistency table."""
    line_data: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        player = str(r.get("Player","") or "").strip()
        team   = str(r.get("Team","") or r.get("Sport","") or "").strip()
        prop   = str(r.get("Prop Type","") or r.get("Prop","") or "Unknown Prop").strip()
        side   = str(r.get("Dir","") or r.get("Direction","") or "—").strip().upper()
        if side not in ("OVER", "UNDER"):
            side = "—"
        if not player or player.lower() in ("none","nan",""):
            continue
        key = (player, prop, side)
        if key not in line_data:
            line_data[key] = {"team": team, "hits":0, "misses":0, "decided":0}
        result = str(r.get("Result","") or r.get("Grade","") or "").strip().upper()
        if result in ("HIT","WIN","1","TRUE","YES","W"):
            line_data[key]["hits"]    += 1
            line_data[key]["decided"] += 1
        elif result in ("MISS","LOSS","0","FALSE","NO","L"):
            line_data[key]["misses"]  += 1
            line_data[key]["decided"] += 1

    # fallback: pre-aggregated
    if not any(v["decided"] > 0 for v in line_data.values()):
        line_data2: dict[tuple[str, str, str], dict] = {}
        for r in rows:
            player = str(r.get("Player","") or "").strip()
            team   = str(r.get("Team","") or "").strip()
            prop   = str(r.get("Prop Type","") or r.get("Prop","") or "Unknown Prop").strip()
            side   = str(r.get("Dir","") or r.get("Direction","") or "—").strip().upper()
            if side not in ("OVER", "UNDER"):
                side = "—"
            if not player or player.lower() in ("none","nan",""):
                continue
            key = (player, prop, side)
            if key not in line_data2:
                line_data2[key] = {"team": team, "hits":0, "misses":0, "decided":0}
            line_data2[key]["hits"]    += safe_int(r.get("Hits",0))
            line_data2[key]["misses"]  += safe_int(r.get("Misses",0))
            line_data2[key]["decided"] += safe_int(r.get("Decided",0))
        line_data = line_data2

    candidates = []
    for (name, prop, side), v in line_data.items():
        d = v["decided"]
        if d < min_decided:
            continue
        hr = v["hits"]/d*100
        candidates.append({
            "name":name,"team":v["team"],"prop":prop,"side":side,
            "hits":v["hits"],"misses":v["misses"],"decided":d,"hit_rate":hr,
            "inconsistency":abs(hr - 50.0),
        })

    if not candidates:
        return f'<div class="muted-note">No player-prop lines with ≥{min_decided} decided props.</div>'

    if top:
        # Most consistent winners: strongest hit rate with useful sample.
        candidates.sort(key=lambda x: (x["hit_rate"], x["decided"]), reverse=True)
    else:
        # Most inconsistent: closest to coin-flip first, with larger sample.
        candidates.sort(key=lambda x: (x["inconsistency"], -x["decided"]))
    candidates = candidates[:limit]

    rows_html = ""
    for c in candidates:
        side_color = "var(--cyan)" if c["side"] == "OVER" else ("var(--gold)" if c["side"] == "UNDER" else "var(--muted)")
        line_lbl = f'{h(c["side"])} {h(c["prop"])}' if c["side"] != "—" else h(c["prop"])
        rows_html += f"""<tr>
          <td><strong>{h(c['name'])}</strong></td>
          <td class="mono muted">{h(c['team'])}</td>
          <td class="mono" style="color:{side_color}">{line_lbl}</td>
          <td class="right mono">{fmt_num(c['decided'])}</td>
          <td class="right mono pos">{fmt_num(c['hits'])}</td>
          <td class="right mono neg">{fmt_num(c['misses'])}</td>
          <td>{rate_bar_html(c['hit_rate'])}</td>
        </tr>"""
    return f"""<div class="table-wrap"><table>
      <thead><tr><th>PLAYER</th><th>TEAM</th><th>LINE</th><th class="right">DEC</th><th class="right">H</th><th class="right">M</th><th>RATE</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>"""


def def_tier_table(rows: list[dict], min_decided: int = 10) -> str:
    dt_agg = build_agg_from_rows(rows, "Def Tier")
    if not dt_agg:
        return ""
    # Sort by a defined order
    order = {
        "elite": 0,
        "above avg": 1,
        "avg": 2,
        "average": 2,
        "below avg": 3,
        "below average": 3,
        "weak": 4,
        "very weak": 5,
    }
    dt_agg.sort(key=lambda x: order.get(x["key"].lower().replace("🟢","").replace("🟡","").replace("🔴","").strip(), 99))

    def _norm_def_tier_early(x: str) -> str:
        return (
            str(x or "")
            .lower()
            .replace("🟢", "")
            .replace("🟡", "")
            .replace("🔴", "")
            .strip()
        )

    rows_html = ""
    for d in dt_agg:
        if d["decided"] == 0: continue
        dkey0 = _norm_def_tier_early(d["key"])
        sub_main = [r for r in rows if _norm_def_tier_early(r.get("Def Tier", "")) == dkey0]
        ou_main = over_under_lines_html(sub_main)
        rows_html += f"""<tr class="{'player-hit' if d['hit_rate']>=55 else ('player-miss' if d['hit_rate']<48 else 'player-warn')}">
          <td style="vertical-align:top"><span style="font-weight:700">{h(d['key'])}</span>{ou_main}</td>
          <td class="right mono">{fmt_num(d['decided'])}</td>
          <td class="right mono">{fmt_num(d['hits'])}</td>
          <td>{rate_bar_html(d['hit_rate'])}</td>
        </tr>"""
    if not rows_html:
        return ""
    # Breakdown matrices inside each defense tier (pick type + ticket tier)
    def _norm_def_tier(x: str) -> str:
        return (
            str(x or "")
            .lower()
            .replace("🟢", "")
            .replace("🟡", "")
            .replace("🔴", "")
            .strip()
        )

    def _stats_cell(s: dict[str, float]) -> str:
        if not s or s.get("decided", 0) == 0:
            return "—"
        col = rate_color(s.get("hit_rate", 0))
        return f'<span style="color:{col};font-weight:700">{pct(s["hit_rate"])}</span> <span class="muted-note" style="font-size:12px;padding:0">({fmt_num(s["decided"])})</span>'

    combined_subgrid, n_sub_ge = _def_tier_combined_subgrid_table(rows, min_decided=min_decided)

    detail_rows_std_dir = ""
    picktype_by_tier: dict[str, list[dict]] = {"goblin": [], "standard": [], "demon": []}
    for d in dt_agg:
        if d["decided"] == 0:
            continue
        dkey = _norm_def_tier(d["key"])
        sub = [r for r in rows if _norm_def_tier(r.get("Def Tier", "")) == dkey]
        if not sub:
            continue

        gob = pick_type_stats(sub, "goblin")
        std = pick_type_stats(sub, "standard")
        dem = pick_type_stats(sub, "demon")
        std_over_rows = [
            r for r in sub
            if "standard" in str(r.get("Pick Type", "")).lower()
            and normalize_bet_direction(r.get("Dir") or r.get("Direction")) == "OVER"
        ]
        std_under_rows = [
            r for r in sub
            if "standard" in str(r.get("Pick Type", "")).lower()
            and normalize_bet_direction(r.get("Dir") or r.get("Direction")) == "UNDER"
        ]
        std_over = overall_stats(std_over_rows) if std_over_rows else {"decided": 0, "hit_rate": 0}
        std_under = overall_stats(std_under_rows) if std_under_rows else {"decided": 0, "hit_rate": 0}

        picktype_by_tier["goblin"].append({"def_tier": d["key"], **gob})
        picktype_by_tier["standard"].append({"def_tier": d["key"], **std})
        picktype_by_tier["demon"].append({"def_tier": d["key"], **dem})

        detail_rows_std_dir += f"""<tr>
          <td><strong>{h(d["key"])}</strong></td>
          <td class="mono">{_stats_cell(std_over)}</td>
          <td class="mono">{_stats_cell(std_under)}</td>
        </tr>"""

    detail_tables = ""
    if combined_subgrid or detail_rows_std_dir:
        def _picktype_sorted_table(kind: str, label: str) -> str:
            rows_k = [x for x in picktype_by_tier.get(kind, []) if x.get("decided", 0) > 0]
            if not rows_k:
                return ""
            rows_k.sort(key=lambda x: (x.get("hit_rate", 0), x.get("decided", 0)), reverse=True)
            body = ""
            for r in rows_k:
                hr = float(r.get("hit_rate", 0))
                row_cls = "player-hit" if hr >= 55 else ("player-miss" if hr < 48 else "player-warn")
                dtk = _norm_def_tier(str(r.get("def_tier", "")))
                slice_ou = [
                    x
                    for x in rows
                    if _norm_def_tier(x.get("Def Tier", "")) == dtk
                    and kind.lower() in str(x.get("Pick Type", "") or "").lower()
                ]
                ou_line = over_under_lines_html(slice_ou)
                body += f"""<tr class="{row_cls}">
          <td style="vertical-align:top"><strong>{h(str(r.get("def_tier", "")))}</strong>{ou_line}</td>
          <td class="right mono">{fmt_num(r.get("decided", 0))}</td>
          <td class="right mono pos">{fmt_num(r.get("hits", 0))}</td>
          <td>{rate_bar_html(hr)}</td>
        </tr>"""
            return f"""<div>
        <div class="section-label">DEF TIER BREAKDOWN — {label} (SORTED)</div>
        <div class="table-wrap"><table>
          <thead><tr><th>DEF TIER</th><th class="right">DECIDED</th><th class="right">HITS</th><th>HIT RATE</th></tr></thead>
          <tbody>{body}</tbody>
        </table></div>
      </div>"""

        split_picktype_tables = f"""<div class="three-col" style="margin-top:12px">
      {_picktype_sorted_table("goblin", "GOBLIN")}
      {_picktype_sorted_table("standard", "STANDARD")}
      {_picktype_sorted_table("demon", "DEMON")}
    </div>"""

        detail_tables = f"""<div style="margin-top:12px">
      <div class="section-label">DEF TIER BREAKDOWN — PICK TYPE × RANK TIER (COMBINED)</div>
      <p style="font-size:12px;color:var(--muted2);margin:4px 0 10px;line-height:1.45">
        Rows = opponent def tier. Columns = pick type × rank tier (A–D).
        Goblin/Demon = OVER only. Standard split OVER/UNDER.
        {n_sub_ge} of 80 opponent-tier × column cells have ≥ {int(min_decided)} decided; others still list rates when 1 ≤ n &lt; {int(min_decided)}.
        <strong>—</strong> = no decided props in that bucket.
        Note: Standard Tier B may be structurally sparse when ml_prob is compressed below tier thresholds — “—” is expected until calibration / a retrain restores spread.
      </p>
      {combined_subgrid}
    </div>
    <div style="margin-top:12px">
      <div class="section-label">DEF TIER BREAKDOWN — STANDARD (OVER/UNDER)</div>
      <div class="table-wrap"><table>
        <thead><tr><th>DEF TIER</th><th>STANDARD OVER</th><th>STANDARD UNDER</th></tr></thead>
        <tbody>{detail_rows_std_dir}</tbody>
      </table></div>
    </div>
    {split_picktype_tables}"""

    return f"""<div class="section-label">HIT RATE BY OPPONENT DEFENSIVE TIER</div>
    <div class="table-wrap" style="margin-bottom:20px"><table>
      <thead><tr><th>DEF TIER</th><th class="right">DECIDED</th><th class="right">HITS</th><th>HIT RATE</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>
    {detail_tables}"""


# ══════════════════════════════════════════════════════════════════════════════
#  SPORT SECTION BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_sport_section(rows: list[dict], sport: str, icon: str) -> str:
    if not rows:
        return ""

    if sport.strip().upper() == "MLB" and (not (icon or "").strip() or (icon or "").strip().upper() == "MLB"):
        icon = "⚾"

    stats  = overall_stats(rows)
    total_label = fmt_num(stats["total"]) if stats["total"] > 0 else fmt_num(stats["decided"] + stats["voids"])
    # Apply the pick-type x tier analysis uniformly across all sport sections.
    matrix_section = pick_tier_direction_matrix_html(rows, min_decided=10)
    split_section = pick_type_tier_direction_split_html(rows, min_decided=10)

    # ── Def Tier ───────────────────────────────────────────────────────────────
    def_section = def_tier_table(rows)

    # ── Prop Types ─────────────────────────────────────────────────────────────
    def _prop_table_for_subset(sub_rows: list[dict], min_decided: int = 5, limit: int = 8) -> str:
        if not sub_rows:
            return '<div class="muted-note">No rows for this split.</div>'
        p_agg = build_agg_from_rows(sub_rows, "Prop Type")
        if not p_agg:
            p_agg = build_agg_from_rows(sub_rows, "Prop")
        p_agg = [p for p in p_agg if p.get("decided", 0) >= min_decided]
        p_agg.sort(key=lambda x: x.get("hit_rate", 0), reverse=True)
        p_agg = p_agg[:limit]
        return prop_type_table(p_agg, "PROP TYPE", min_decided=0) if p_agg else f'<div class="muted-note">No prop types with ≥{min_decided} decided.</div>'

    prop_agg = build_agg_from_rows(rows, "Prop Type")
    if not prop_agg:
        prop_agg = build_agg_from_rows(rows, "Prop")
    prop_agg.sort(key=lambda x: x["hit_rate"], reverse=True)
    top_props  = [p for p in prop_agg if p["decided"] >= 10][:10]
    worst_props= sorted([p for p in prop_agg if p["decided"] >= 10], key=lambda x: x["hit_rate"])[:8]

    top_prop_tbl   = prop_type_table(top_props,   "PROP TYPE",  min_decided=0)
    worst_prop_tbl = prop_type_table(worst_props, "PROP TYPE",  min_decided=0)

    prop_section = ""
    if top_props or worst_props:
        # Split charts the user asked for: tier / pick type / over-under / vs def tier
        by_picktype = f"""<div class="three-col">
          <div><div class="section-label">STANDARD — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if "standard" in str(r.get("Pick Type","") or "").lower()], min_decided=5)}</div>
          <div><div class="section-label">GOBLIN — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if "goblin" in str(r.get("Pick Type","") or "").lower()], min_decided=5)}</div>
          <div><div class="section-label">DEMON — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if "demon" in str(r.get("Pick Type","") or "").lower()], min_decided=5)}</div>
        </div>"""

        by_dir = f"""<div class="two-col">
          <div><div class="section-label">OVER — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if str(r.get("Dir","") or r.get("Direction","")).strip().upper() == "OVER"], min_decided=5)}</div>
          <div><div class="section-label">UNDER — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if str(r.get("Dir","") or r.get("Direction","")).strip().upper() == "UNDER"], min_decided=5)}</div>
        </div>"""

        by_tier = f"""<div class="two-col">
          <div><div class="section-label">TIER A — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if str(r.get("Tier","") or "").strip().upper() == "A"], min_decided=5)}</div>
          <div><div class="section-label">TIER B — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if str(r.get("Tier","") or "").strip().upper() == "B"], min_decided=5)}</div>
        </div>
        <div class="two-col">
          <div><div class="section-label">TIER C — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if str(r.get("Tier","") or "").strip().upper() == "C"], min_decided=5)}</div>
          <div><div class="section-label">TIER D — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if str(r.get("Tier","") or "").strip().upper() == "D"], min_decided=5)}</div>
        </div>"""

        def _norm_def_tier_for_split(x: str) -> str:
            return str(x or "").lower().replace("🟢","").replace("🟡","").replace("🔴","").strip()

        by_def = f"""<div class="two-col">
          <div><div class="section-label">VS ELITE/ABOVE AVG DEF — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if _norm_def_tier_for_split(r.get("Def Tier","")) in ("elite","above avg")], min_decided=5)}</div>
          <div><div class="section-label">VS AVG / BELOW AVG / WEAK DEF — TOP PROP TYPES (≥5 DECIDED)</div>{_prop_table_for_subset([r for r in rows if _norm_def_tier_for_split(r.get("Def Tier","")) in ("avg","average","below avg","below average","weak","very weak")], min_decided=5)}</div>
        </div>"""

        prop_section = f"""<div class="section-label">PROP TYPE BREAKDOWNS</div>
        {by_picktype}
        {by_dir}
        {by_tier}
        {by_def}
        <div class="two-col">
          <div>
            <div class="section-label">OVERALL TOP PROP TYPES BY HIT RATE (≥10 DECIDED)</div>
            {top_prop_tbl}
          </div>
          <div>
            <div class="section-label">OVERALL WORST PROP TYPES (≥10 DECIDED)</div>
            {worst_prop_tbl}
          </div>
        </div>"""

    # ── Player Leaderboards ────────────────────────────────────────────────────
    top_players   = player_table(rows, top=True,  min_decided=3, limit=8)
    worst_players = player_table(rows, top=False, min_decided=3, limit=8)

    player_section = f"""<div class="two-col">
      <div>
        <div class="section-label">🏆 MOST CONSISTENT WINNERS (PLAYER + PROP LINE)</div>
        {top_players}
      </div>
      <div>
        <div class="section-label">💀 MOST INCONSISTENT LINES (PLAYER + PROP LINE)</div>
        {worst_players}
      </div>
    </div>"""

    return f"""<details class="sport-section sport-collapsible">
    <summary>
      <div class="sport-header">
        <div class="sport-label">{icon} {sport}</div>
        <div class="sport-header-line"></div>
        <div class="sport-meta-count">{total_label} TOTAL PROPS</div>
      </div>
    </summary>
    <div class="sport-section-body">
      {matrix_section}
      {split_section}
      {def_section}
      {prop_section}
      {player_section}
    </div>
  </details>"""


# ══════════════════════════════════════════════════════════════════════════════
#  TAKEAWAYS / INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════

def _takeaway_sport_snippets(
    bundles: list[tuple[str, list[dict]]],
) -> str:
    """One-line per sport hit rate for the takeaway summary (only sports with decided props)."""
    parts: list[str] = []
    for label, rows in bundles:
        if not rows:
            continue
        st = overall_stats(rows)
        if st["decided"] <= 0:
            continue
        parts.append(f"<strong>{h(label)}</strong>: {pct(st['hit_rate'])} ({fmt_num(st['decided'])} dec)")
    return (" · ".join(parts)) if parts else ""


def build_takeaways(
    nba_rows: list[dict],
    cbb_rows: list[dict],
    nhl_rows: list[dict] | None = None,
    soccer_rows: list[dict] | None = None,
    mlb_rows: list[dict] | None = None,
) -> str:
    """
    Insight cards at the bottom of slate_eval. Uses **all loaded sports** so Railway /grades
    shows combined takeaways (not NBA-only) when MLB/NHL/Soccer are graded without NBA.
    """
    nhl_rows = nhl_rows or []
    soccer_rows = soccer_rows or []
    mlb_rows = mlb_rows or []

    all_rows: list[dict] = (
        list(nba_rows) + list(cbb_rows) + list(nhl_rows) + list(soccer_rows) + list(mlb_rows)
    )

    insights: list[str] = []
    alerts: list[str] = []

    def add_insight(icon: str, title: str, body: str) -> None:
        insights.append(f"""<div class="insight-card">
      <div class="insight-icon">{icon}</div>
      <div class="insight-title">{h(title)}</div>
      <div class="insight-body">{body}</div>
    </div>""")

    if not all_rows:
        add_insight("📊", "No Data", "No graded props found for this date.")
        insights_html = "\n".join(insights)
        return f"""<div class="sport-section">
    <div class="sport-header">
      <div class="sport-label">📋 TAKEAWAYS</div>
      <div class="sport-header-line"></div>
    </div>
    <div class="insight-grid">{insights_html}</div>
  </div>"""

    all_stats = overall_stats(all_rows)
    bundles = [
        ("NBA", nba_rows),
        ("CBB", cbb_rows),
        ("NHL", nhl_rows),
        ("Soccer", soccer_rows),
        ("MLB", mlb_rows),
    ]
    sport_line = _takeaway_sport_snippets(bundles)

    # ── Combined overall summary (all sports) ─────────────────────────────
    add_insight(
        "📋",
        "Overall Slate Summary (all sports)",
        f"Combined: <strong>{pct(all_stats['hit_rate'])}</strong> on "
        f"{fmt_num(all_stats['decided'])} decided props."
        + (f"<br/><span style=\"opacity:0.92\">{sport_line}</span>" if sport_line else ""),
    )

    # Tier A + Goblin — aggregated across every sport in the slate
    ta_all = tier_a_stats(all_rows)
    gb_all = pick_type_stats(all_rows, "goblin")
    if ta_all["decided"] > 0 or gb_all["decided"] > 0:
        ta_str = f"Tier A: <strong>{pct(ta_all['hit_rate'])}</strong> ({fmt_num(ta_all['decided'])} dec)." if ta_all["decided"] > 0 else ""
        gb_str = f" Goblin: <strong>{pct(gb_all['hit_rate'])}</strong> ({fmt_num(gb_all['decided'])} dec)." if gb_all["decided"] > 0 else ""
        body = (ta_str + " " + gb_str).strip()
        if ta_all["hit_rate"] >= 60 or (gb_all["decided"] > 0 and gb_all["hit_rate"] >= 58):
            add_insight("✅", "Tier A & Goblin (all sports)", body)
        else:
            add_insight("⚠️", "Tier A & Goblin (all sports)", body)

    # Demon — all sports
    dem_all = pick_type_stats(all_rows, "demon")
    if dem_all["decided"] > 0:
        demon_str = f"Demons (all sports): <strong>{pct(dem_all['hit_rate'])}</strong> on {fmt_num(dem_all['decided'])} decided."
        if dem_all["hit_rate"] < 45:
            add_insight(
                "🚨",
                "Demon Line Performance (all sports)",
                demon_str + " Demon hit rate is well below breakeven — monitor before including in slips.",
            )
            alerts.append(
                f'<div class="alert alert-red"><div class="alert-title">🚨 Demon lines (all sports) — '
                f'{pct(dem_all["hit_rate"])} on {fmt_num(dem_all["decided"])} decided</div>'
                f"Demon hit rate is well below breakeven. Exclude from slips until further notice.</div>"
            )
        else:
            add_insight(
                "📊",
                "Demon Line Performance (all sports)",
                demon_str + " Monitor demon performance before including in slips.",
            )

    # Over vs Under — all sports (matches ALL SPORTS matrix scope)
    over_all = overall_stats(
        [r for r in all_rows if str(r.get("Dir", "") or r.get("Direction", "")).strip().upper() == "OVER"]
    )
    under_all = overall_stats(
        [r for r in all_rows if str(r.get("Dir", "") or r.get("Direction", "")).strip().upper() == "UNDER"]
    )
    if over_all["decided"] > 0 and under_all["decided"] > 0:
        add_insight(
            "📈",
            "Over vs Under (all sports)",
            f"OVERs: <strong>{pct(over_all['hit_rate'])}</strong> ({fmt_num(over_all['decided'])} dec). "
            f"UNDERs: <strong>{pct(under_all['hit_rate'])}</strong> ({fmt_num(under_all['decided'])} dec).",
        )

    insights_html = "\n".join(insights)
    alerts_html = "\n".join(alerts)

    return f"""<div class="sport-section">
    <div class="sport-header">
      <div class="sport-label">📋 TAKEAWAYS</div>
      <div class="sport-header-line"></div>
    </div>
    {alerts_html}
    <div class="insight-grid">{insights_html}</div>
  </div>"""


def sport_label(s: str) -> str:
    return {"NBA": "🏀 NBA", "CBB": "🎓 CBB", "NHL": "🏒 NHL", "MLB": "⚾ MLB", "SOCCER": "⚽ Soccer"}.get(
        s.upper(), s
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FILE DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def find_graded_file(sport: str, date_str: str) -> Path | None:
    """Search common locations for nba_graded_{date}.xlsx or cbb_graded_{date}.xlsx."""
    pattern = f"graded_{sport.lower()}_{date_str}.xlsx"
    search_dirs = [
        SCRIPT_DIR,
        SCRIPT_DIR / "outputs",
        SCRIPT_DIR / "outputs" / date_str,
        OUTPUTS_DIR,
        OUTPUTS_DIR / date_str,
        Path.cwd(),
        Path.cwd() / "outputs",
        Path.cwd() / "outputs" / date_str,
        # project root outputs/{date}/ — e.g. PropOracle/outputs/2026-03-06/
        ROOT_DIR / "outputs" / date_str,
        ROOT_DIR / "outputs",
        ROOT_DIR,
    ]
    for d in search_dirs:
        p = d / pattern
        if p.exists():
            return p
    # glob fallback
    for d in search_dirs:
        if d.exists():
            matches = list(d.glob(f"*{sport.lower()}*graded*{date_str}*.xlsx"))
            if matches:
                return matches[0]
    return None


def load_merged_nba_graded_rows(date_str: str) -> list[dict]:
    """Full-game NBA plus NBA1Q/NBA1H graded workbooks, merged for one NBA bucket in Prop Evaluation."""
    rows: list[dict] = []
    for key in ("nba", "nba1q", "nba1h"):
        p = find_graded_file(key, date_str)
        if p:
            rows.extend(load_graded(p))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
#  FULL HTML
# ══════════════════════════════════════════════════════════════════════════════

NAV_HTML = ""

CSS = """
:root{
  --glass:rgba(255,255,255,0.03);
  --glass-bd:rgba(255,255,255,0.08);
  --bg:transparent;
  --text:rgba(232,236,255,0.95);
  --muted:#94a3b8;
  --muted2:rgba(255,255,255,0.48);
  --gold:#f0a500;
  --gold2:#d4a017;
  --cyan:#00e5ff;
  --green:#39ff6e;
  --amber:#e8b84a;
  --red:#ff4d4d;
  --purple:#c4a5ff;
  --pending:#666;
}
*{box-sizing:border-box;margin:0;padding:0}
html{height:100%;overflow-x:hidden;overflow-y:auto;-webkit-overflow-scrolling:touch;background:#0a0a14}
body{font-family:'Inter',sans-serif;background:#0a0a14;color:var(--text);min-height:100%;height:auto;max-height:none;margin:0;overflow-x:hidden;overflow-y:visible;padding-bottom:max(10px, env(safe-area-inset-bottom, 0px));font-size:clamp(14px,1.02vw,16px);line-height:1.45}
h1,h2,h3,h4,h5,h6{font-family:'Bebas Neue',sans-serif}
header,.main{position:relative;z-index:1}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:rgba(255,255,255,0.04)}::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.14);border-radius:4px}

header{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
border:1px solid var(--glass-bd);border-left:none;border-right:none;border-radius:0;padding:18px 20px;display:flex;flex-direction:column;align-items:stretch;gap:0;
box-shadow:0 8px 32px rgba(0,0,0,.28)}
.slate-header-top{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;width:100%}
.grades-hub-toolbar-host{flex:1 1 100%;align-self:stretch;width:100%;max-width:100%;min-height:0;box-sizing:border-box}
.logo{display:flex;align-items:center;gap:14px}
.logo-icon{width:120px;height:120px;object-fit:contain;display:block;filter:drop-shadow(0 0 8px rgba(212,160,23,0.45))}
@media(max-width:768px){.logo-icon{width:80px;height:80px}}
.logo-title{font-family:'Bebas Neue',sans-serif;font-size:28px;letter-spacing:3px;background:linear-gradient(to bottom,#f0a500,#d4a017,#f7e08a);-webkit-background-clip:text;background-clip:text;color:transparent}
.logo-sub{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted);letter-spacing:2.2px;margin-top:2px}
.date-badge{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted2);background:var(--glass);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
border:1px solid var(--glass-bd);border-radius:999px;padding:8px 14px;letter-spacing:1.5px}
.main{max-width:none;width:100%;margin:0;padding:24px 20px;box-sizing:border-box}
.sport-header{display:flex;align-items:center;gap:14px;margin-bottom:22px;flex-wrap:wrap;min-width:0}
.sport-label{font-family:'Bebas Neue',sans-serif;font-size:32px;letter-spacing:4px;line-height:1;color:var(--gold);text-shadow:0 0 28px rgba(240,165,0,.18)}
.sport-header-line{flex:1;min-width:80px;height:1px;background:rgba(255,255,255,0.08)}
.sport-meta-count{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted2)}
.sport-section{margin-bottom:48px;width:100%;max-width:100%;box-sizing:border-box}
.sport-section:last-child{margin-bottom:0}
.sport-collapsible{border:1px solid var(--glass-bd);border-radius:14px;padding:12px 14px;background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);box-shadow:0 4px 24px rgba(0,0,0,.18)}
.sport-collapsible>summary{list-style:none;cursor:pointer}
.sport-collapsible>summary::-webkit-details-marker{display:none}
.sport-collapsible>summary::marker{display:none;content:''}
.sport-collapsible>summary .sport-header{margin-bottom:0}
.sport-collapsible>summary .sport-label::before{content:'▸ ';color:var(--gold)}
.sport-collapsible[open]>summary .sport-label::before{content:'▾ '}
.sport-collapsible .sport-section-body{padding-top:14px}
.matrix-collapsible{margin:6px 0 20px;border:1px solid var(--glass-bd);border-radius:14px;background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);box-shadow:0 4px 24px rgba(0,0,0,.18);overflow:hidden}
.matrix-collapsible>summary{list-style:none;cursor:pointer;padding:12px 14px;font-family:'Bebas Neue',sans-serif;font-size:clamp(14px,1.08vw,16px);letter-spacing:2px;color:var(--muted);border-bottom:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;gap:8px}
.matrix-collapsible>summary::-webkit-details-marker{display:none}
.matrix-collapsible>summary::before{content:'▸';color:var(--gold);transition:transform .15s ease}
.matrix-collapsible[open]>summary::before{transform:rotate(90deg)}
.matrix-body{padding:12px}
.matrix-summary{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted2);margin:0 0 10px}
.matrix-collapsible tr.matrix-hit td{background:rgba(57,255,110,0.06)}
.matrix-collapsible tr.matrix-hit td:first-child{border-left:3px solid var(--green)}
.matrix-collapsible tr.matrix-miss td{background:rgba(255,77,77,0.10)}
.matrix-collapsible tr.matrix-miss td:first-child{border-left:3px solid var(--red)}
.matrix-collapsible tr.matrix-warn td{background:rgba(240,165,0,0.06)}
.matrix-collapsible tr.matrix-warn td:first-child{border-left:3px solid var(--gold)}
.matrix-collapsible tr.matrix-empty td,.matrix-collapsible tr.matrix-sparse td{opacity:.88}
.matrix-collapsible tr.matrix-sparse td:first-child{border-left:3px dashed rgba(255,255,255,0.12)}
.section-label{font-family:'Bebas Neue',sans-serif;font-size:clamp(14px,1.08vw,16px);color:var(--muted);letter-spacing:2.5px;display:flex;align-items:center;gap:10px;margin-bottom:16px}
.section-label::after{content:'';flex:1;height:1px;background:rgba(255,255,255,0.08)}
.stat-grid{display:grid;gap:14px;margin-bottom:24px;width:100%;max-width:100%;box-sizing:border-box}
.stat-grid-4{grid-template-columns:repeat(4,1fr)}
.stat-grid-2{grid-template-columns:repeat(2,1fr)}
.stat-card{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
border:1px solid var(--glass-bd);border-radius:14px;padding:16px 18px;position:relative;overflow:hidden;transition:border-color .2s,box-shadow .2s;
box-shadow:0 4px 24px rgba(0,0,0,.22)}
.stat-card:hover{border-color:rgba(255,255,255,0.12);box-shadow:0 8px 32px rgba(0,0,0,.28)}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.stat-card.green::before{background:linear-gradient(90deg,var(--green),transparent)}
.stat-card.blue::before{background:linear-gradient(90deg,var(--cyan),transparent)}
.stat-card.amber::before{background:linear-gradient(90deg,var(--gold),transparent)}
.stat-card.red::before{background:linear-gradient(90deg,var(--red),transparent)}
.stat-card.purple::before{background:linear-gradient(90deg,var(--purple),transparent)}
.stat-label{font-family:'Bebas Neue',sans-serif;font-size:11px;color:var(--muted);letter-spacing:2.2px;margin-bottom:8px}
.stat-val{font-family:'Bebas Neue',sans-serif;font-size:36px;letter-spacing:2px;line-height:1}
.stat-sub{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted2);margin-top:5px}
.stat-sub strong{font-weight:700}
.table-wrap{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
border:1px solid var(--glass-bd);border-radius:14px;overflow:hidden;margin-bottom:20px;box-shadow:0 4px 24px rgba(0,0,0,.18)}
table{width:100%;border-collapse:collapse;font-size:clamp(13px,1.05vw,15px);font-family:'Inter',sans-serif}
th{font-family:'Bebas Neue',sans-serif;font-size:clamp(14px,1.15vw,16px);letter-spacing:2px;color:var(--muted);padding:10px 12px;text-align:left;
background:rgba(0,0,0,0.22);backdrop-filter:blur(12px);border-bottom:1px solid var(--glass-bd);white-space:nowrap}
th.right{text-align:right}
td{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.06);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.04)}
td.right{text-align:right}td.mono{font-family:'Inter',sans-serif;font-size:clamp(13px,1.05vw,15px)}
.rate-cell{display:flex;align-items:center;gap:10px}
.rate-bar-bg{flex:1;height:6px;background:rgba(255,255,255,.08);border-radius:3px;overflow:hidden}
.rate-bar-fill{height:100%;border-radius:3px;transition:width .4s}
.rate-num{font-family:'Inter',sans-serif;font-size:clamp(13px,1.05vw,15px);min-width:48px;text-align:right;flex-shrink:0}
.chip{display:inline-flex;align-items:center;flex-shrink:0;min-width:max-content;border-radius:8px;padding:3px 10px;font-size:12px;font-weight:700;font-family:'Bebas Neue',sans-serif;letter-spacing:.35px;
background:rgba(255,255,255,0.04);backdrop-filter:blur(12px);border:1px solid var(--glass-bd);box-sizing:border-box;vertical-align:middle;white-space:nowrap!important;word-break:normal!important;overflow-wrap:normal!important;hyphens:none!important;max-width:none}
.chip-a,.chip-b,.chip-c,.chip-d,.chip-goblin,.chip-demon,.chip-std{display:inline-flex;align-items:center;flex-shrink:0;min-width:max-content;white-space:nowrap!important;word-break:normal!important;overflow-wrap:normal!important;hyphens:none!important}
.chip-a{background:rgba(57,255,110,.08);color:var(--green);border-color:rgba(57,255,110,.28)}
.chip-b{background:rgba(0,229,255,.08);color:var(--cyan);border-color:rgba(0,229,255,.28)}
.chip-c{background:rgba(240,165,0,.08);color:var(--gold);border-color:rgba(240,165,0,.3)}
.chip-d{background:rgba(255,255,255,.04);color:var(--muted);border-color:var(--glass-bd)}
.chip-goblin{background:rgba(196,165,255,.10);color:var(--purple);border-color:rgba(196,165,255,.32)}
.chip-demon{background:rgba(255,77,77,.10);color:var(--red);border-color:rgba(255,77,77,.32)}
.chip-std{background:rgba(0,229,255,.08);color:var(--cyan);border-color:rgba(0,229,255,.25)}
.two-col{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-bottom:20px;width:100%;max-width:100%;box-sizing:border-box}
.three-col{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:20px;width:100%;max-width:100%;box-sizing:border-box}
.insight-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:24px;width:100%;max-width:100%;box-sizing:border-box;min-width:0}
.sport-section:last-child .insight-grid{margin-bottom:0}
/* Last card is alone on its row when count is 1,4,7,… — span full width instead of a narrow left column */
.insight-grid>.insight-card:last-child:nth-child(3n+1){grid-column:1 / -1}
.insight-card{background:var(--glass);backdrop-filter:blur(20px);border:1px solid var(--glass-bd);border-radius:12px;padding:14px 16px;box-shadow:0 4px 20px rgba(0,0,0,.15);min-width:0;box-sizing:border-box}
.insight-icon{font-size:22px;margin-bottom:8px}
.insight-title{font-weight:700;font-size:14px;margin-bottom:6px;font-family:'Bebas Neue',sans-serif;letter-spacing:1px;color:var(--gold)}
.insight-body{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted2);line-height:1.6}
.insight-body strong{color:var(--text)}
tr.player-hit td{background:rgba(57,255,110,0.04)}
tr.player-hit td:first-child{border-left:3px solid var(--green)}
tr.player-miss td{background:rgba(255,77,77,0.08)}
tr.player-miss td:first-child{border-left:3px solid var(--red)}
tr.player-warn td{background:rgba(240,165,0,0.06)}
tr.player-warn td:first-child{border-left:3px solid var(--gold)}
.pos{color:var(--green);font-weight:700}.neg{color:var(--red);font-weight:700}.neu{color:var(--muted2)}
.alert{border-radius:12px;padding:14px 18px;margin-bottom:20px;border:1px solid;font-size:13px;line-height:1.6;backdrop-filter:blur(16px);max-width:100%;box-sizing:border-box}
.alert-red{background:rgba(255,77,77,.08);border-color:rgba(255,77,77,.35)}
.alert-green{background:rgba(57,255,110,.08);border-color:rgba(57,255,110,.32)}
.alert-amber{background:rgba(240,165,0,.08);border-color:rgba(240,165,0,.32)}
.alert-title{font-family:'Bebas Neue',sans-serif;font-weight:700;font-size:13px;letter-spacing:1px;margin-bottom:4px}
.sub-dir{display:inline-block;margin-right:8px}
.muted-note{font-family:'Inter',sans-serif;font-size:12px;color:var(--muted);padding:14px;text-align:center}
td.muted{color:var(--muted2)}
@media(max-width:768px){
.stat-grid{justify-items:start;justify-content:start}
.stat-grid-4,.stat-grid-2{grid-template-columns:repeat(auto-fill,minmax(min(100%,9rem),max-content))}
.two-col,.three-col{grid-template-columns:1fr}
.insight-grid{display:grid;grid-template-columns:1fr;gap:14px;width:100%;max-width:100%}
.insight-grid>.insight-card:last-child:nth-child(3n+1){grid-column:auto}
.insight-card{width:100%;max-width:100%;min-width:0}
.stat-card{width:fit-content;max-width:100%}
.alert{width:fit-content;max-width:100%}
.rate-num{min-width:min(48px,max-content)}
body{font-size:clamp(14px,3.2vw,16px)}
table{font-size:clamp(12px,3.2vw,14px)}
td.mono{font-size:clamp(12px,3.2vw,14px)}
.rate-num{font-size:clamp(12px,3.2vw,14px)}
th{font-size:clamp(12px,3.4vw,14px);padding:8px 8px}
td{padding:8px 8px}
.section-label{font-size:clamp(12px,3.2vw,14px)}
.sport-label{font-size:28px}
.logo-title{font-size:24px}
.two-col.pick-tier-split{grid-template-columns:1fr!important;gap:18px!important}
}
/* Touch / hub iframe: BY PICK TYPE + BY TIER stack when viewport math is wrong (wide iframe on a phone). */
@media(pointer:coarse){
.two-col.pick-tier-split{grid-template-columns:1fr!important;gap:18px!important}
}
/* v20260429mobilecol — ≤960px: tables/chips only (no blanket .two-col stack — desktop keeps 2-col). */
@media(max-width:960px){
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.table-wrap table{min-width:0;width:100%;max-width:100%}
.two-col.pick-tier-split .table-wrap table{min-width:0}
.sport-header{flex-wrap:wrap;gap:6px}
.sport-meta-count{font-size:clamp(10px,2.5vw,12px)}
.chip,.chip-a,.chip-b,.chip-c,.chip-d,.chip-goblin,.chip-demon,.chip-std{font-size:clamp(10px,2.6vw,12px);padding:4px 8px;letter-spacing:.06em;display:inline-flex!important;align-items:center!important;flex-shrink:0!important;min-width:max-content!important;white-space:nowrap!important;word-break:normal!important;overflow-wrap:normal!important;hyphens:none!important;max-width:none!important}
.table-wrap .sub-dir{display:block;margin:5px 0 0;font-size:clamp(11px,2.8vw,13px);line-height:1.35}
.ou-breakdown{font-size:clamp(11px,2.8vw,13px)!important;line-height:1.4!important}
.two-col.pick-tier-split td.mono{font-size:clamp(11px,2.8vw,13px)}
.two-col.pick-tier-split td:first-child,.two-col.pick-tier-split th:first-child{min-width:0}
.two-col.pick-tier-split .table-wrap td{vertical-align:top}
.two-col.pick-tier-split .table-wrap th{white-space:normal;word-break:normal;line-height:1.2}
}
th[data-sort-key]{cursor:pointer;user-select:none;position:relative;padding-right:1.35em}
th[data-sort-key]:hover{color:var(--text)}
th[data-sort-key]:focus-visible{outline:2px solid var(--cyan);outline-offset:2px;border-radius:4px}
th.sort-active.sort-asc::after{content:'\\25B2';font-size:0.55em;opacity:0.9;position:absolute;right:6px;top:50%;transform:translateY(-50%);letter-spacing:0}
th.sort-active.sort-desc::after{content:'\\25BC';font-size:0.55em;opacity:0.9;position:absolute;right:6px;top:50%;transform:translateY(-50%);letter-spacing:0}
"""


TABLE_SORT_JS = """
<script>
(function () {
  function pickOrder(tr, c) {
    var t = (tr.cells[c].textContent || "").toLowerCase();
    if (t.indexOf("goblin") >= 0) return 2;
    if (t.indexOf("demon") >= 0) return 3;
    if (t.indexOf("standard") >= 0) return 1;
    return 0;
  }
  function tierOrder(tr, c) {
    var m = (tr.cells[c].textContent || "").match(/TIER\\s*([ABCD])/i);
    if (!m) return 0;
    return "ABCD".indexOf(m[1].toUpperCase()) + 1;
  }
  function dirOrder(tr, c) {
    var t = tr.cells[c].textContent || "";
    if (/OVER/i.test(t)) return 1;
    if (/UNDER/i.test(t)) return 2;
    return 0;
  }
  function numCell(tr, c) {
    var t = (tr.cells[c].textContent || "").trim().replace(/,/g, "");
    if (t === "\\u2014" || t === "-" || t === "") return null;
    var n = parseInt(t, 10);
    return isNaN(n) ? null : n;
  }
  function pctCell(tr, c) {
    var t = (tr.cells[c].textContent || "").trim();
    if (t === "\\u2014" || t === "-" || t === "") return null;
    var m = t.match(/([\\d.]+)\\s*%/);
    if (m) return parseFloat(m[1]);
    return null;
  }
  function barPct(tr, c) {
    var td = tr.cells[c];
    var fill = td.querySelector(".rate-bar-fill");
    if (fill && fill.style && fill.style.width) {
      var w = parseFloat(fill.style.width);
      if (!isNaN(w)) return w;
    }
    return pctCell(tr, c);
  }
  var extractors = {
    pick: pickOrder,
    tier: tierOrder,
    dir: dirOrder,
    decided: numCell,
    hits: numCell,
    misses: numCell,
    rate: pctCell,
    bar: barPct
  };
  function cmp(a, b, key, col, dir) {
    var ex = extractors[key];
    if (!ex) return 0;
    var va = ex(a, col);
    var vb = ex(b, col);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    if (va < vb) return -dir;
    if (va > vb) return dir;
    return 0;
  }
  function initTable(table) {
    var ths = table.querySelectorAll("thead th[data-sort-key]");
    ths.forEach(function (th) {
      th.setAttribute("tabindex", "0");
      th.setAttribute("role", "button");
      var col = th.cellIndex;
      var key = th.getAttribute("data-sort-key");
      function applySort() {
        var prevCol = parseInt(table.getAttribute("data-sort-col") || "-1", 10);
        var prevDir = parseInt(table.getAttribute("data-sort-dir") || "1", 10);
        var dir = prevCol === col ? -prevDir : 1;
        table.setAttribute("data-sort-col", String(col));
        table.setAttribute("data-sort-dir", String(dir));
        table.querySelectorAll("thead th.sort-active").forEach(function (x) {
          x.classList.remove("sort-active", "sort-asc", "sort-desc");
        });
        th.classList.add("sort-active", dir === 1 ? "sort-asc" : "sort-desc");
        var tbody = table.querySelector("tbody");
        if (!tbody) return;
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        rows.sort(function (r1, r2) { return cmp(r1, r2, key, col, dir); });
        rows.forEach(function (r) { tbody.appendChild(r); });
      }
      th.addEventListener("click", applySort);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          applySort();
        }
      });
    });
  }
  document.querySelectorAll("table.table-sortable").forEach(initTable);
})();
</script>
"""


def build_html(date_str: str, nba_rows: list[dict], cbb_rows: list[dict],
               nba_path: Path | None, cbb_path: Path | None,
               nhl_rows: list[dict] | None = None,
               soccer_rows: list[dict] | None = None,
               mlb_rows: list[dict] | None = None,
               nhl_path: Path | None = None,
               soccer_path: Path | None = None,
               mlb_path: Path | None = None) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        display_date = d.strftime("%b %d, %Y").upper()
    except ValueError:
        display_date = date_str.upper()

    nhl_rows    = nhl_rows    or []
    soccer_rows = soccer_rows or []
    mlb_rows    = mlb_rows    or []
    all_rows = list(nba_rows) + list(cbb_rows) + list(nhl_rows) + list(soccer_rows) + list(mlb_rows)
    all_section = build_sport_section(all_rows, "ALL SPORTS", "🌐") if all_rows else ""
    nba_section    = build_sport_section(nba_rows,    "NBA",    "🏀") if nba_rows    else ""
    cbb_section    = build_sport_section(cbb_rows,    "CBB",    "🎓") if cbb_rows    else ""
    nhl_section    = build_sport_section(nhl_rows,    "NHL",    "🏒") if nhl_rows    else ""
    soccer_section = build_sport_section(soccer_rows, "Soccer", "⚽") if soccer_rows else ""
    mlb_section    = build_sport_section(mlb_rows,    "MLB",    "⚾") if mlb_rows    else ""
    takeaways = build_takeaways(
        nba_rows,
        cbb_rows,
        nhl_rows=nhl_rows,
        soccer_rows=soccer_rows,
        mlb_rows=mlb_rows,
    )

    if not nba_section and not cbb_section and not nhl_section and not soccer_section and not mlb_section:
        body_content = """<div style="text-align:center;padding:60px 20px;font-family:'Inter',sans-serif">
          <div style="font-size:32px;margin-bottom:16px">📭</div>
          <div style="font-size:18px;color:rgba(255,255,255,0.55)">No graded data found for this date.</div>
          <div style="font-size:13px;color:rgba(255,255,255,0.4);margin-top:8px">
            Run <code style="color:var(--cyan)">run_grader.ps1 --date {date_str}</code> to generate grades.
          </div>
        </div>""".replace("{date_str}", date_str)
    else:
        body_content = all_section + nba_section + cbb_section + nhl_section + soccer_section + mlb_section + takeaways

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover"/>
<meta name="theme-color" content="#0a0a14"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<title>Slate Eval — {h(display_date)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="/static/global-scrollbar.css?v=20260416"/>
<link rel="stylesheet" href="/static/light-theme-dim-overrides.css?v=20260419perf2"/>
<link rel="stylesheet" href="/static/proporacle-mobile-schema.css?v=20260430schemapage"/>
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="slate-header-top">
    <div class="logo">
      <img src="/static/proporacle-logo-v3.png?v=20260320b" alt="PropORACLE logo" class="logo-icon"/>
      <div>
        <div class="logo-title">SLATE EVALUATION</div>
        <div class="logo-sub">POST-GAME GRADE REPORT</div>
      </div>
    </div>
    <div class="date-badge">📅 {h(display_date)}</div>
  </div>
  <div id="proporacle-grades-toolbar-host" class="grades-hub-toolbar-host"></div>
</header>
<div class="main">
{body_content}
</div>
{TABLE_SORT_JS}
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate slate_eval_{date}.html from nba/cbb graded xlsx files."
    )
    parser.add_argument("--date", type=str, default="",
                        help="Date string YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--nba",  type=str, default="",
                        help="Path to nba_graded_*.xlsx")
    parser.add_argument("--cbb",  type=str, default="",
                        help="Path to cbb_graded_*.xlsx")
    parser.add_argument("--nhl",  type=str, default="",
                        help="Path to nhl_graded_*.xlsx")
    parser.add_argument("--soccer", type=str, default="",
                        help="Path to soccer_graded_*.xlsx")
    parser.add_argument("--mlb", type=str, default="",
                        help="Path to graded_mlb_*.xlsx")
    parser.add_argument("--out",  type=str, default="",
                        help="Output path or directory (default: next to this script)")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Write slate_eval even when no graded xlsx files exist (empty / no-data UI).",
    )
    args = parser.parse_args()

    # Resolve date
    if args.date:
        date_str = args.date.strip()
    else:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"  Date: {date_str}")

    # Resolve file paths
    nba_path: Path | None = None
    cbb_path: Path | None = None

    if args.nba:
        nba_path = Path(args.nba).resolve()
        if not nba_path.exists():
            print(f"  WARNING: NBA file not found: {nba_path}")
            nba_path = None
    else:
        nba_path = find_graded_file("nba", date_str)
        if nba_path:
            print(f"  Auto-detected NBA: {nba_path}")
        else:
            print(f"  WARNING: Could not auto-detect nba_graded_{date_str}.xlsx")

    if args.cbb:
        cbb_path = Path(args.cbb).resolve()
        if not cbb_path.exists():
            print(f"  WARNING: CBB file not found: {cbb_path}")
            cbb_path = None
    else:
        cbb_path = find_graded_file("cbb", date_str)
        if cbb_path:
            print(f"  Auto-detected CBB: {cbb_path}")
        else:
            print(f"  WARNING: Could not auto-detect cbb_graded_{date_str}.xlsx")

    nhl_path: Path | None = None
    if args.nhl:
        nhl_path = Path(args.nhl).resolve()
        if not nhl_path.exists():
            print(f"  WARNING: NHL file not found: {nhl_path}")
            nhl_path = None
    else:
        nhl_path = find_graded_file("nhl", date_str)
        if nhl_path:
            print(f"  Auto-detected NHL: {nhl_path}")

    nba1q_path: Path | None = find_graded_file("nba1q", date_str)
    if nba1q_path:
        print(f"  Auto-detected NBA1Q: {nba1q_path}")
    nba1h_path: Path | None = find_graded_file("nba1h", date_str)
    if nba1h_path:
        print(f"  Auto-detected NBA1H: {nba1h_path}")

    soccer_path: Path | None = None
    if args.soccer:
        soccer_path = Path(args.soccer).resolve()
        if not soccer_path.exists():
            print(f"  WARNING: Soccer file not found: {soccer_path}")
            soccer_path = None
    else:
        soccer_path = find_graded_file("soccer", date_str)
        if soccer_path:
            print(f"  Auto-detected Soccer: {soccer_path}")

    mlb_path: Path | None = None
    if args.mlb:
        mlb_path = Path(args.mlb).resolve()
        if not mlb_path.exists():
            print(f"  WARNING: MLB file not found: {mlb_path}")
            mlb_path = None
    else:
        mlb_path = find_graded_file("mlb", date_str)
        if mlb_path:
            print(f"  Auto-detected MLB: {mlb_path}")

    if not nba_path and not nba1q_path and not nba1h_path and not cbb_path and not nhl_path and not soccer_path and not mlb_path:
        if args.allow_empty:
            print("  NOTE: No graded files; emitting empty slate eval (--allow-empty).")
        else:
            print("  ERROR: No graded files found. Specify --nba/--cbb/--nhl/--soccer/--mlb.")
            sys.exit(1)

    # Load rows
    nba_rows: list[dict] = []
    cbb_rows: list[dict] = []
    if nba_path:
        print(f"  Loading NBA: {nba_path.name} ...", end="", flush=True)
        nba_rows = load_graded(nba_path)
        print(f" {len(nba_rows):,} rows")
    nba1q_rows: list[dict] = []
    if nba1q_path:
        print(f"  Loading NBA1Q: {nba1q_path.name} ...", end="", flush=True)
        nba1q_rows = load_graded(nba1q_path)
        print(f" {len(nba1q_rows):,} rows")
    nba1h_rows: list[dict] = []
    if nba1h_path:
        print(f"  Loading NBA1H: {nba1h_path.name} ...", end="", flush=True)
        nba1h_rows = load_graded(nba1h_path)
        print(f" {len(nba1h_rows):,} rows")
    nba_rows_merged = [*nba_rows, *nba1q_rows, *nba1h_rows]
    if cbb_path:
        print(f"  Loading CBB: {cbb_path.name} ...", end="", flush=True)
        cbb_rows = load_graded(cbb_path)
        print(f" {len(cbb_rows):,} rows")

    nhl_rows: list[dict] = []
    if nhl_path:
        print(f"  Loading NHL: {nhl_path.name} ...", end="", flush=True)
        nhl_rows = load_graded(nhl_path)
        print(f" {len(nhl_rows):,} rows")

    soccer_rows: list[dict] = []
    if soccer_path:
        print(f"  Loading Soccer: {soccer_path.name} ...", end="", flush=True)
        soccer_rows = load_graded(soccer_path)
        print(f" {len(soccer_rows):,} rows")

    mlb_rows: list[dict] = []
    if mlb_path:
        print(f"  Loading MLB: {mlb_path.name} ...", end="", flush=True)
        mlb_rows = load_graded(mlb_path)
        print(f" {len(mlb_rows):,} rows")

    # Build HTML (use merged NBA so 1Q/1H rows appear in Slate Evaluation, not only in JSON)
    print("  Building HTML ...", end="", flush=True)
    html = build_html(date_str, nba_rows_merged, cbb_rows, nba_path, cbb_path,
                      nhl_rows=nhl_rows, soccer_rows=soccer_rows, mlb_rows=mlb_rows,
                      nhl_path=nhl_path, soccer_path=soccer_path, mlb_path=mlb_path)
    print(f" {len(html):,} bytes")

    # Resolve output path
    out_name = f"slate_eval_{date_str}.html"
    if args.out:
        out_p = Path(args.out).resolve()
        if out_p.is_dir() or args.out.endswith(("\\","/")):
            out_p = out_p / out_name
        else:
            out_p = out_p  # treat as full file path
    else:
        out_p = ROOT_DIR / "ui_runner" / "templates" / out_name

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(html, encoding="utf-8")
    print(f"  Saved  -> {out_p}")

    json_p = export_graded_props_json(
        date_str,
        out_p.parent,
        [
            ("NBA", nba_rows_merged),
            ("CBB", cbb_rows),
            ("NHL", nhl_rows),
            ("Soccer", soccer_rows),
            ("MLB", mlb_rows),
        ],
    )
    print(f"  Saved  -> {json_p}")
    print("  Done.")


if __name__ == "__main__":
    main()
