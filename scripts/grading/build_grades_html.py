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

def load_graded(path: Path) -> list[dict]:
    """Load all rows from all sheets of a graded xlsx, normalizing column names."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: list[dict] = []
    for shname in wb.sheetnames:
        ws = wb[shname]
        rows.extend(read_sheet(ws))
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
#  AGGREGATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  HTML FRAGMENT BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def pick_type_row(label: str, icon: str, agg: dict, extra_html: str = "") -> str:
    d   = agg["decided"]
    h_  = agg["hits"]
    m   = agg["misses"]
    hr  = agg["hit_rate"]
    return f"""<tr>
      <td><span class="chip chip-{label.lower()}">{icon} {label}</span>{extra_html}</td>
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
        dir_html += f'<span style="color:var(--cyan);font-size:10px;margin-right:8px">▲ </span><span style="color:{col};font-size:10px;margin-right:8px">{over_pct}</span>'
    if under_pct != "—":
        col = "var(--green)" if pct_f(under_pct) >= 55 else "var(--red)"
        dir_html += f'<span style="color:var(--gold);font-size:10px">▼ </span><span style="color:{col};font-size:10px">{under_pct}</span>'
    tier_lbl = f"TIER {tier.upper()}"
    return f"""<tr class="{row_cls}">
      <td><span class="chip {chip_cls}">{tier_lbl}</span>
      <div style="font-size:10px;color:var(--muted2);margin-top:3px">{dir_html}{extra}</div></td>
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
    """Build top/worst player performer table."""
    player_data: dict[str, dict] = {}
    for r in rows:
        player = str(r.get("Player","") or "").strip()
        team   = str(r.get("Team","") or r.get("Sport","") or "").strip()
        if not player or player.lower() in ("none","nan",""): continue
        if player not in player_data:
            player_data[player] = {"team": team, "hits":0, "misses":0, "decided":0}
        result = str(r.get("Result","") or r.get("Grade","") or "").strip().upper()
        if result in ("HIT","WIN","1","TRUE","YES","W"):
            player_data[player]["hits"]    += 1
            player_data[player]["decided"] += 1
        elif result in ("MISS","LOSS","0","FALSE","NO","L"):
            player_data[player]["misses"]  += 1
            player_data[player]["decided"] += 1

    # fallback: pre-aggregated
    if not any(v["decided"] > 0 for v in player_data.values()):
        player_data2: dict[str, dict] = {}
        for r in rows:
            player = str(r.get("Player","") or "").strip()
            team   = str(r.get("Team","") or "").strip()
            if not player or player.lower() in ("none","nan",""): continue
            if player not in player_data2:
                player_data2[player] = {"team": team, "hits":0, "misses":0, "decided":0}
            player_data2[player]["hits"]    += safe_int(r.get("Hits",0))
            player_data2[player]["misses"]  += safe_int(r.get("Misses",0))
            player_data2[player]["decided"] += safe_int(r.get("Decided",0))
        player_data = player_data2

    candidates = []
    for name, v in player_data.items():
        d = v["decided"]
        if d < min_decided: continue
        hr = v["hits"]/d*100
        candidates.append({"name":name,"team":v["team"],"hits":v["hits"],"misses":v["misses"],"decided":d,"hit_rate":hr})

    if not candidates:
        return f'<div class="muted-note">No players with ≥{min_decided} decided props.</div>'

    candidates.sort(key=lambda x: x["hit_rate"], reverse=top)
    candidates = candidates[:limit]

    rows_html = ""
    for c in candidates:
        col = "var(--green)" if c["hit_rate"] >= 55 else "var(--red)"
        rows_html += f"""<tr>
          <td><strong>{h(c['name'])}</strong></td>
          <td class="mono muted">{h(c['team'])}</td>
          <td class="right mono pos">{fmt_num(c['hits'])}</td>
          <td class="right mono neg">{fmt_num(c['misses'])}</td>
          <td>{rate_bar_html(c['hit_rate'])}</td>
        </tr>"""
    return f"""<div class="table-wrap"><table>
      <thead><tr><th>PLAYER</th><th>TEAM</th><th class="right">H</th><th class="right">M</th><th>RATE</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>"""


def def_tier_table(rows: list[dict]) -> str:
    dt_agg = build_agg_from_rows(rows, "Def Tier")
    if not dt_agg:
        return ""
    # Sort by a defined order
    order = {"elite":0,"above avg":1,"avg":2,"average":2,"weak":3,"very weak":4}
    dt_agg.sort(key=lambda x: order.get(x["key"].lower().replace("🟢","").replace("🟡","").replace("🔴","").strip(), 99))
    rows_html = ""
    for d in dt_agg:
        if d["decided"] == 0: continue
        rows_html += f"""<tr class="{'player-hit' if d['hit_rate']>=55 else ('player-miss' if d['hit_rate']<48 else 'player-warn')}">
          <td><span style="font-weight:700">{h(d['key'])}</span></td>
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
        return f'<span style="color:{col};font-weight:700">{pct(s["hit_rate"])}</span> <span class="muted-note" style="font-size:10px;padding:0">({fmt_num(s["decided"])})</span>'

    detail_rows_pt = ""
    detail_rows_tier = ""
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

        detail_rows_pt += f"""<tr>
          <td><strong>{h(d["key"])}</strong></td>
          <td class="mono">{_stats_cell(gob)}</td>
          <td class="mono">{_stats_cell(std)}</td>
          <td class="mono">{_stats_cell(dem)}</td>
        </tr>"""

        t_cells = []
        for t in ("A", "B", "C", "D"):
            t_rows = [r for r in sub if str(r.get("Tier", "") or "").strip().upper() == t]
            t_stats = overall_stats(t_rows) if t_rows else {"decided": 0, "hit_rate": 0}
            t_cells.append(f'<td class="mono">{_stats_cell(t_stats)}</td>')
        detail_rows_tier += f"""<tr>
          <td><strong>{h(d["key"])}</strong></td>
          {''.join(t_cells)}
        </tr>"""

    detail_tables = ""
    if detail_rows_pt or detail_rows_tier:
        detail_tables = f"""<div class="two-col" style="margin-top:12px">
      <div>
        <div class="section-label">DEF TIER BREAKDOWN — BY PICK TYPE</div>
        <div class="table-wrap"><table>
          <thead><tr><th>DEF TIER</th><th>GOBLIN</th><th>STANDARD</th><th>DEMON</th></tr></thead>
          <tbody>{detail_rows_pt}</tbody>
        </table></div>
      </div>
      <div>
        <div class="section-label">DEF TIER BREAKDOWN — BY TIER (A/B/C/D)</div>
        <div class="table-wrap"><table>
          <thead><tr><th>DEF TIER</th><th>TIER A</th><th>TIER B</th><th>TIER C</th><th>TIER D</th></tr></thead>
          <tbody>{detail_rows_tier}</tbody>
        </table></div>
      </div>
    </div>"""

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

    stats  = overall_stats(rows)
    tier_a = tier_a_stats(rows)
    goblin = pick_type_stats(rows, "goblin")

    total_label = fmt_num(stats["total"]) if stats["total"] > 0 else fmt_num(stats["decided"] + stats["voids"])

    # ── KPI cards ──────────────────────────────────────────────────────────────
    hr_col = rate_color(stats["hit_rate"])
    ta_col = rate_color(tier_a["hit_rate"])
    gb_col = rate_color(goblin["hit_rate"])
    ta_val = pct(tier_a["hit_rate"]) if tier_a["decided"] > 0 else "—"
    gb_val = pct(goblin["hit_rate"]) if goblin["decided"] > 0 else "—"
    void_pct = f'{stats["voids"]/stats["total"]*100:.1f}%' if stats["total"] > 0 else "—"

    kpi_html = f"""<div class="section-label">OVERALL PERFORMANCE</div>
    <div class="stat-grid stat-grid-4" style="margin-bottom:20px">
      <div class="stat-card green">
        <div class="stat-label">OVERALL HIT RATE</div>
        <div class="stat-val" style="color:{hr_col}">{pct(stats['hit_rate'])}</div>
        <div class="stat-sub">{fmt_num(stats['hits'])} hits / {fmt_num(stats['decided'])} decided</div>
      </div>
      <div class="stat-card blue">
        <div class="stat-label">TOTAL PROPS</div>
        <div class="stat-val" style="color:var(--cyan)">{total_label}</div>
        <div class="stat-sub"><strong>{fmt_num(stats['voids'])}</strong> voids ({void_pct})</div>
      </div>
      <div class="stat-card amber">
        <div class="stat-label">TIER A HIT RATE</div>
        <div class="stat-val" style="color:{ta_col}">{ta_val}</div>
        <div class="stat-sub">{fmt_num(tier_a['hits'])} hits / {fmt_num(tier_a['decided'])} decided</div>
      </div>
      <div class="stat-card purple">
        <div class="stat-label">GOBLIN HIT RATE</div>
        <div class="stat-val" style="color:{gb_col}">{gb_val}</div>
        <div class="stat-sub">{fmt_num(goblin['hits'])} hits / {fmt_num(goblin['decided'])} decided</div>
      </div>
    </div>"""

    # ── By Pick Type ───────────────────────────────────────────────────────────
    gob_s = pick_type_stats(rows, "goblin")
    dem_s = pick_type_stats(rows, "demon")
    std_s = pick_type_stats(rows, "standard")

    # Over/Under for Standard
    std_over  = overall_stats([r for r in rows if "standard" in str(r.get("Pick Type","")).lower()
                                and str(r.get("Dir","") or r.get("Direction","")).strip().upper() == "OVER"])
    std_under = overall_stats([r for r in rows if "standard" in str(r.get("Pick Type","")).lower()
                                and str(r.get("Dir","") or r.get("Direction","")).strip().upper() == "UNDER"])

    std_dir_html = ""
    if std_over["decided"] > 0:
        col = "var(--green)" if std_over["hit_rate"] >= 55 else "var(--red)"
        std_dir_html += f'<div style="font-size:10px;color:var(--muted2);margin-top:4px"><div class="sub-dir"><span style="color:var(--cyan);font-size:10px">▲ OVER</span> <span style="color:{col}">{pct(std_over["hit_rate"])}</span> ({fmt_num(std_over["decided"])} dec)</div>'
    if std_under["decided"] > 0:
        col = "var(--green)" if std_under["hit_rate"] >= 55 else "var(--red)"
        std_dir_html += f'<div class="sub-dir"><span style="color:var(--gold);font-size:10px">▼ UNDER</span> <span style="color:{col}">{pct(std_under["hit_rate"])}</span> ({fmt_num(std_under["decided"])} dec)</div></div>'

    pick_table = f"""<div class="table-wrap"><table>
      <thead><tr><th>TYPE</th><th class="right">DECIDED</th><th class="right">HITS</th><th class="right">MISSES</th><th>HIT RATE</th></tr></thead>
      <tbody>
        {pick_type_row("Goblin","🎃", gob_s)}
        {pick_type_row("Demon","😈",  dem_s)}
        <tr>
          <td><span class="chip chip-std">⭐ Standard</span>{std_dir_html}</td>
          <td class="right mono">{fmt_num(std_s['decided'])}</td>
          <td class="right mono pos">{fmt_num(std_s['hits'])}</td>
          <td class="right mono neg">{fmt_num(std_s['misses'])}</td>
          <td>{rate_bar_html(std_s['hit_rate'])}</td>
        </tr>
      </tbody>
    </table></div>"""

    # ── By Tier ────────────────────────────────────────────────────────────────
    tier_rows_html = ""
    for t in ["A","B","C","D"]:
        t_rows = [r for r in rows if str(r.get("Tier","") or "").strip().upper() == t]
        if not t_rows: continue
        t_stats = overall_stats(t_rows)
        if t_stats["decided"] == 0:
            t_stats = build_agg_from_rows(t_rows, "_tier_x_")
            if t_stats: t_stats = t_stats[0]; t_stats["hit_rate"] = t_stats.get("hit_rate",0)
            else: continue
        d  = t_stats["decided"]
        h_ = t_stats["hits"]
        hr = t_stats["hit_rate"]
        chip_cls = {"A":"chip-a","B":"chip-b","C":"chip-c"}.get(t,"chip-d")
        row_cls  = "player-hit" if hr>=55 else ("player-miss" if hr<48 else "player-warn")
        tier_rows_html += f"""<tr class="{row_cls}">
          <td><span class="chip {chip_cls}">TIER {t}</span></td>
          <td class="right mono">{fmt_num(d)}</td>
          <td class="right mono pos">{fmt_num(h_)}</td>
          <td>{rate_bar_html(hr)}</td>
        </tr>"""

    tier_table = f"""<div class="table-wrap"><table>
      <thead><tr><th>TIER</th><th class="right">DECIDED</th><th class="right">HITS</th><th>HIT RATE</th></tr></thead>
      <tbody>{tier_rows_html}</tbody>
    </table></div>"""

    two_col = f"""<div class="two-col">
      <div>
        <div class="section-label">BY PICK TYPE</div>
        {pick_table}
      </div>
      <div>
        <div class="section-label">BY TIER</div>
        {tier_table}
      </div>
    </div>"""

    # ── Def Tier ───────────────────────────────────────────────────────────────
    def_section = def_tier_table(rows)

    # ── Prop Types ─────────────────────────────────────────────────────────────
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
        prop_section = f"""<div class="two-col">
          <div>
            <div class="section-label">TOP PROP TYPES BY HIT RATE (≥10 DECIDED)</div>
            {top_prop_tbl}
          </div>
          <div>
            <div class="section-label">WORST PROP TYPES (≥10 DECIDED)</div>
            {worst_prop_tbl}
          </div>
        </div>"""

    # ── Player Leaderboards ────────────────────────────────────────────────────
    top_players   = player_table(rows, top=True,  min_decided=5, limit=8)
    worst_players = player_table(rows, top=False, min_decided=5, limit=8)

    player_section = f"""<div class="two-col">
      <div>
        <div class="section-label">🏆 TOP {sport} PERFORMERS</div>
        {top_players}
      </div>
      <div>
        <div class="section-label">💀 WORST {sport} PERFORMERS</div>
        {worst_players}
      </div>
    </div>"""

    return f"""<div class="sport-section">
    <div class="sport-header">
      <div class="sport-label">{icon} {sport}</div>
      <div class="sport-header-line"></div>
      <div class="sport-meta-count">{total_label} TOTAL PROPS</div>
    </div>

    {kpi_html}
    {two_col}
    {def_section}
    {prop_section}
    {player_section}
  </div>"""


# ══════════════════════════════════════════════════════════════════════════════
#  TAKEAWAYS / INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════

def build_takeaways(nba_rows: list[dict], cbb_rows: list[dict]) -> str:
    insights = []
    alerts   = []

    def add_insight(icon, title, body):
        insights.append(f"""<div class="insight-card">
      <div class="insight-icon">{icon}</div>
      <div class="insight-title">{h(title)}</div>
      <div class="insight-body">{body}</div>
    </div>""")

    # NBA insights
    if nba_rows:
        nba_stats = overall_stats(nba_rows)
        nba_tier_a = tier_a_stats(nba_rows)
        nba_goblin = pick_type_stats(nba_rows, "goblin")
        nba_demon  = pick_type_stats(nba_rows, "demon")
        nba_std    = pick_type_stats(nba_rows, "standard")

        # Tier A + Goblin
        if nba_tier_a["decided"] > 0 or nba_goblin["decided"] > 0:
            ta_str = f"NBA Tier A: <strong>{pct(nba_tier_a['hit_rate'])}</strong>." if nba_tier_a["decided"] > 0 else ""
            gb_str = f" NBA Goblin: <strong>{pct(nba_goblin['hit_rate'])}</strong>." if nba_goblin["decided"] > 0 else ""
            if cbb_rows:
                cbb_gob = pick_type_stats(cbb_rows, "goblin")
                if cbb_gob["decided"] > 0:
                    gb_str += f" CBB Goblin: <strong>{pct(cbb_gob['hit_rate'])}</strong>."
            if nba_tier_a["hit_rate"] >= 60 or nba_goblin["hit_rate"] >= 58:
                add_insight("✅", "Tier A & Goblin Performance", ta_str + gb_str)
            else:
                add_insight("⚠️", "Tier A & Goblin Performance", ta_str + gb_str)

        # Demon alert
        if nba_demon["decided"] > 0:
            demon_str = f"NBA Demons: <strong>{pct(nba_demon['hit_rate'])}</strong>."
            if cbb_rows:
                cbb_dem = pick_type_stats(cbb_rows, "demon")
                if cbb_dem["decided"] > 0:
                    demon_str += f" CBB Demons: <strong>{pct(cbb_dem['hit_rate'])}</strong>."
            if nba_demon["hit_rate"] < 45:
                add_insight("🚨", "Demon Line Performance", demon_str + " Demon hit rate is well below breakeven — monitor before including in slips.")
                alerts.append(f'<div class="alert alert-red"><div class="alert-title">🚨 {sport_label("NBA")} Demon Lines — {pct(nba_demon["hit_rate"])} on {fmt_num(nba_demon["decided"])} decided</div>Demon hit rate is well below breakeven. Exclude from slips until further notice.</div>')
            else:
                add_insight("📊", "Demon Line Performance", demon_str + " Monitor demon performance before including in slips.")

        # Overall summary
        cbb_str = ""
        if cbb_rows:
            cbb_s = overall_stats(cbb_rows)
            cbb_str = f" CBB: <strong>{pct(cbb_s['hit_rate'])}</strong> overall ({fmt_num(cbb_s['decided'])} decided)."
        add_insight("📋", "Overall Slate Summary",
            f"NBA: <strong>{pct(nba_stats['hit_rate'])}</strong> overall ({fmt_num(nba_stats['decided'])} decided).{cbb_str}")

        # Over vs Under
        nba_over  = overall_stats([r for r in nba_rows if str(r.get("Dir","") or r.get("Direction","")).strip().upper() == "OVER"])
        nba_under = overall_stats([r for r in nba_rows if str(r.get("Dir","") or r.get("Direction","")).strip().upper() == "UNDER"])
        if nba_over["decided"] > 0 and nba_under["decided"] > 0:
            add_insight("📈", "Over vs Under Performance",
                f"NBA OVERs: <strong>{pct(nba_over['hit_rate'])}</strong>. NBA UNDERs: <strong>{pct(nba_under['hit_rate'])}</strong>.")

    # Worst NBA players (alerts)
    if nba_rows:
        for r in nba_rows:
            player = str(r.get("Player","") or "").strip()
            result = str(r.get("Result","") or r.get("Grade","") or "").strip().upper()
            # individual bad performers flagged in alerts

    if not insights:
        add_insight("📊","No Data","No graded props found for this date.")

    insights_html = "\n".join(insights)
    alerts_html   = "\n".join(alerts)

    return f"""<div class="sport-section">
    <div class="sport-header">
      <div class="sport-label">📋 TAKEAWAYS</div>
      <div class="sport-header-line"></div>
    </div>
    {alerts_html}
    <div class="insight-grid">{insights_html}</div>
  </div>"""


def sport_label(s: str) -> str:
    return {"NBA":"🏀 NBA","CBB":"🎓 CBB"}.get(s.upper(), s)


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
body{font-family:'Share Tech Mono',monospace;background:transparent;color:var(--text);min-height:100vh;padding-bottom:60px}
h1,h2,h3,h4,h5,h6{font-family:'Bebas Neue',sans-serif}
body::before{content:'';position:fixed;top:-20%;left:-10%;width:55%;height:55%;background:radial-gradient(ellipse,rgba(212,160,23,.07) 0%,transparent 70%);pointer-events:none;z-index:0}
body::after{content:'';position:fixed;bottom:-20%;right:-10%;width:50%;height:50%;background:radial-gradient(ellipse,rgba(0,229,255,.06) 0%,transparent 70%);pointer-events:none;z-index:0}
header,.main{position:relative;z-index:1}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:rgba(255,255,255,0.04)}::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.14);border-radius:4px}

header{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
border:1px solid var(--glass-bd);border-radius:0 0 18px 18px;margin:0 12px;padding:18px 28px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;
box-shadow:0 8px 32px rgba(0,0,0,.28)}
.logo{display:flex;align-items:center;gap:14px}
.logo-icon{width:120px;height:120px;object-fit:contain;display:block;filter:drop-shadow(0 0 8px rgba(212,160,23,0.45))}
@media(max-width:768px){.logo-icon{width:80px;height:80px}}
.logo-title{font-family:'Bebas Neue',sans-serif;font-size:28px;letter-spacing:3px;background:linear-gradient(to bottom,#f0a500,#d4a017,#f7e08a);-webkit-background-clip:text;background-clip:text;color:transparent}
.logo-sub{font-family:'Share Tech Mono',monospace;font-size:10px;color:var(--muted);letter-spacing:2.5px;margin-top:2px}
.date-badge{font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--muted2);background:var(--glass);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
border:1px solid var(--glass-bd);border-radius:999px;padding:8px 16px;letter-spacing:1.5px}
.main{max-width:1100px;margin:0 auto;padding:24px 20px}
.sport-header{display:flex;align-items:center;gap:14px;margin-bottom:22px;flex-wrap:wrap}
.sport-label{font-family:'Bebas Neue',sans-serif;font-size:32px;letter-spacing:4px;line-height:1;color:var(--gold);text-shadow:0 0 28px rgba(240,165,0,.18)}
.sport-header-line{flex:1;min-width:80px;height:1px;background:rgba(255,255,255,0.08)}
.sport-meta-count{font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--muted2)}
.sport-section{margin-bottom:48px}
.section-label{font-family:'Bebas Neue',sans-serif;font-size:11px;color:var(--muted);letter-spacing:3px;display:flex;align-items:center;gap:10px;margin-bottom:16px}
.section-label::after{content:'';flex:1;height:1px;background:rgba(255,255,255,0.08)}
.stat-grid{display:grid;gap:14px;margin-bottom:24px}
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
.stat-label{font-family:'Bebas Neue',sans-serif;font-size:9px;color:var(--muted);letter-spacing:2.5px;margin-bottom:8px}
.stat-val{font-family:'Bebas Neue',sans-serif;font-size:36px;letter-spacing:2px;line-height:1}
.stat-sub{font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--muted2);margin-top:5px}
.stat-sub strong{font-weight:700}
.table-wrap{background:var(--glass);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
border:1px solid var(--glass-bd);border-radius:14px;overflow:hidden;margin-bottom:20px;box-shadow:0 4px 24px rgba(0,0,0,.18)}
table{width:100%;border-collapse:collapse;font-size:13px;font-family:'Share Tech Mono',monospace}
th{font-family:'Bebas Neue',sans-serif;font-size:10px;letter-spacing:2px;color:var(--muted);padding:10px 14px;text-align:left;
background:rgba(0,0,0,0.22);backdrop-filter:blur(12px);border-bottom:1px solid var(--glass-bd);white-space:nowrap}
th.right{text-align:right}
td{padding:10px 14px;border-bottom:1px solid rgba(255,255,255,.06);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.04)}
td.right{text-align:right}td.mono{font-family:'Share Tech Mono',monospace;font-size:12px}
.rate-cell{display:flex;align-items:center;gap:10px}
.rate-bar-bg{flex:1;height:5px;background:rgba(255,255,255,.08);border-radius:3px;overflow:hidden}
.rate-bar-fill{height:100%;border-radius:3px;transition:width .4s}
.rate-num{font-family:'Share Tech Mono',monospace;font-size:12px;width:44px;text-align:right;flex-shrink:0}
.chip{display:inline-block;border-radius:8px;padding:3px 10px;font-size:11px;font-weight:700;font-family:'Bebas Neue',sans-serif;letter-spacing:.5px;
background:rgba(255,255,255,0.04);backdrop-filter:blur(12px);border:1px solid var(--glass-bd)}
.chip-a{background:rgba(57,255,110,.08);color:var(--green);border-color:rgba(57,255,110,.28)}
.chip-b{background:rgba(0,229,255,.08);color:var(--cyan);border-color:rgba(0,229,255,.28)}
.chip-c{background:rgba(240,165,0,.08);color:var(--gold);border-color:rgba(240,165,0,.3)}
.chip-d{background:rgba(255,255,255,.04);color:var(--muted);border-color:var(--glass-bd)}
.chip-goblin{background:rgba(196,165,255,.10);color:var(--purple);border-color:rgba(196,165,255,.32)}
.chip-demon{background:rgba(255,77,77,.10);color:var(--red);border-color:rgba(255,77,77,.32)}
.chip-std{background:rgba(0,229,255,.08);color:var(--cyan);border-color:rgba(0,229,255,.25)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
.insight-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px}
.insight-card{background:var(--glass);backdrop-filter:blur(20px);border:1px solid var(--glass-bd);border-radius:12px;padding:14px 16px;box-shadow:0 4px 20px rgba(0,0,0,.15)}
.insight-icon{font-size:22px;margin-bottom:8px}
.insight-title{font-weight:700;font-size:13px;margin-bottom:6px;font-family:'Bebas Neue',sans-serif;letter-spacing:1px;color:var(--gold)}
.insight-body{font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--muted2);line-height:1.6}
.insight-body strong{color:var(--text)}
tr.player-hit td{background:rgba(57,255,110,0.04)}
tr.player-hit td:first-child{border-left:3px solid var(--green)}
tr.player-miss td{background:rgba(255,77,77,0.08)}
tr.player-miss td:first-child{border-left:3px solid var(--red)}
tr.player-warn td{background:rgba(240,165,0,0.06)}
tr.player-warn td:first-child{border-left:3px solid var(--gold)}
.pos{color:var(--green);font-weight:700}.neg{color:var(--red);font-weight:700}.neu{color:var(--muted2)}
.alert{border-radius:12px;padding:14px 18px;margin-bottom:20px;border:1px solid;font-size:13px;line-height:1.6;backdrop-filter:blur(16px)}
.alert-red{background:rgba(255,77,77,.08);border-color:rgba(255,77,77,.35)}
.alert-green{background:rgba(57,255,110,.08);border-color:rgba(57,255,110,.32)}
.alert-amber{background:rgba(240,165,0,.08);border-color:rgba(240,165,0,.32)}
.alert-title{font-family:'Bebas Neue',sans-serif;font-weight:700;font-size:13px;letter-spacing:1px;margin-bottom:4px}
.sub-dir{display:inline-block;margin-right:8px}
.muted-note{font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--muted);padding:14px;text-align:center}
.footer-gen{font-family:'Share Tech Mono',monospace;font-size:10px;color:var(--muted);text-align:center;margin-top:40px;letter-spacing:1.5px}
td.muted{color:var(--muted2)}
@media(max-width:768px){.stat-grid-4,.stat-grid-2{grid-template-columns:repeat(2,1fr)}.two-col,.insight-grid{grid-template-columns:1fr}}
"""


def build_html(date_str: str, nba_rows: list[dict], cbb_rows: list[dict],
               nba_path: Path | None, cbb_path: Path | None,
               nhl_rows: list[dict] | None = None,
               soccer_rows: list[dict] | None = None,
               nhl_path: Path | None = None,
               soccer_path: Path | None = None) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        display_date = d.strftime("%b %d, %Y").upper()
    except ValueError:
        display_date = date_str.upper()

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    nhl_rows    = nhl_rows    or []
    soccer_rows = soccer_rows or []
    nba_section    = build_sport_section(nba_rows,    "NBA",    "🏀") if nba_rows    else ""
    cbb_section    = build_sport_section(cbb_rows,    "CBB",    "🎓") if cbb_rows    else ""
    nhl_section    = build_sport_section(nhl_rows,    "NHL",    "🏒") if nhl_rows    else ""
    soccer_section = build_sport_section(soccer_rows, "Soccer", "⚽") if soccer_rows else ""
    takeaways   = build_takeaways(nba_rows, cbb_rows)

    sources = []
    if nba_path:    sources.append(nba_path.name)
    if cbb_path:    sources.append(cbb_path.name)
    if nhl_path:    sources.append(nhl_path.name)
    if soccer_path: sources.append(soccer_path.name)
    source_line = " &nbsp;·&nbsp; ".join(h(s) for s in sources)

    if not nba_section and not cbb_section and not nhl_section and not soccer_section:
        body_content = """<div style="text-align:center;padding:60px 20px;font-family:'Share Tech Mono',monospace">
          <div style="font-size:32px;margin-bottom:16px">📭</div>
          <div style="font-size:18px;color:rgba(255,255,255,0.55)">No graded data found for this date.</div>
          <div style="font-size:13px;color:rgba(255,255,255,0.4);margin-top:8px">
            Run <code style="color:var(--cyan)">run_grader.ps1 --date {date_str}</code> to generate grades.
          </div>
        </div>""".replace("{date_str}", date_str)
    else:
        body_content = nba_section + cbb_section + nhl_section + soccer_section + takeaways

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Slate Eval — {h(display_date)}</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&display=swap" rel="stylesheet"/>
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="logo">
    <img src="/static/proporacle-logo-v3.png?v=20260320b" alt="PropORACLE logo" class="logo-icon"/>
    <div>
      <div class="logo-title">SLATE EVALUATION</div>
      <div class="logo-sub">POST-GAME GRADE REPORT</div>
    </div>
  </div>
  <div class="date-badge">📅 {h(display_date)}</div>
</header>
<div class="main">
{body_content}
  <div class="footer-gen">
    GENERATED {h(generated)} &nbsp;·&nbsp; {source_line}
  </div>
</div>
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

    if not nba_path and not cbb_path and not nhl_path and not soccer_path:
        if args.allow_empty:
            print("  NOTE: No graded files; emitting empty slate eval (--allow-empty).")
        else:
            print("  ERROR: No graded files found. Specify --nba/--cbb/--nhl/--soccer.")
            sys.exit(1)

    # Load rows
    nba_rows: list[dict] = []
    cbb_rows: list[dict] = []
    if nba_path:
        print(f"  Loading NBA: {nba_path.name} ...", end="", flush=True)
        nba_rows = load_graded(nba_path)
        print(f" {len(nba_rows):,} rows")
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

    # Build HTML
    print("  Building HTML ...", end="", flush=True)
    html = build_html(date_str, nba_rows, cbb_rows, nba_path, cbb_path,
                      nhl_rows=nhl_rows, soccer_rows=soccer_rows,
                      nhl_path=nhl_path, soccer_path=soccer_path)
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
    print("  Done.")


if __name__ == "__main__":
    main()
