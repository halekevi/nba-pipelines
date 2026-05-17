"""
build_grade_report.py  —  drop in PropOracle root, next to run_grader.ps1
Reads graded_nba/cbb/nhl/soccer_DATE.xlsx → slate_eval_DATE.html
"""
from __future__ import annotations
import argparse, html as _html, sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("ERROR: pip install pandas openpyxl"); sys.exit(1)

# ── paths ──────────────────────────────────────────────────────────────────────
def _find_root() -> Path:
    here = Path(__file__).resolve().parent
    for c in [here, here.parent, here.parent.parent]:
        if (c / "ui_runner").exists(): return c
    return here

BASE_DIR      = _find_root()
TEMPLATES_DIR = BASE_DIR / "ui_runner" / "ui_runner" / "templates"

SPORT_COLOR = {"NBA":"#3b82f6","CBB":"#8b5cf6","NHL":"#06b6d4","SOCCER":"#10b981","MLB":"#f59e0b"}
SPORT_ICON  = {"NBA":"🏀","CBB":"🎓","NHL":"🏒","SOCCER":"⚽","MLB":"⚾"}

# ── tiny helpers ───────────────────────────────────────────────────────────────
h   = lambda v: _html.escape(str(v) if v is not None else "")
pct = lambda n,d: f"{n/d*100:.1f}%" if d else "—"
pf  = lambda n,d: n/d*100 if d else 0.0
rc  = lambda p: "var(--green)" if p>=55 else ("var(--amber)" if p>=45 else "var(--red)")

def hmv(df):
    if "Outcome" not in df.columns: return 0,0,len(df)
    o = df["Outcome"].astype(str).str.upper()
    return int((o=="HIT").sum()), int((o=="MISS").sum()), int((o=="VOID").sum())

def bar(hi,dec):
    p = pf(hi,dec); col = rc(p)
    return (f'<div class="rc"><div class="rb"><div class="rf" style="width:{min(p,100):.1f}%;'
            f'background:{col}"></div></div>'
            f'<div class="rn" style="color:{col}">{pct(hi,dec)}</div></div>')

def pick_chip(v):
    v = str(v).lower()
    if "goblin" in v: return '<span class="chip chip-goblin">🎃 Goblin</span>'
    if "demon"  in v: return '<span class="chip chip-demon">😈 Demon</span>'
    return '<span class="chip chip-std">⭐ Standard</span>'

def tier_chip(v):
    v = str(v).strip().upper()
    cls = {"A":"chip-a","B":"chip-b","C":"chip-c","D":"chip-d"}.get(v,"chip-d")
    return f'<span class="chip {cls}">Tier {h(v)}</span>'

def outcome_cell(v):
    v = str(v).upper()
    if v=="HIT":  return '<span class="pos">✓ HIT</span>'
    if v=="MISS": return '<span class="neg">✗ MISS</span>'
    return '<span class="neu">— VOID</span>'

# ── LOAD & NORMALISE ───────────────────────────────────────────────────────────
# We always read "Box Raw" sheet (first sheet with per-prop rows)
# then rename columns to a canonical set so the rest of the code is sport-agnostic.

NBA_MAP = {
    "player":         "Player",
    "team":           "Team",
    "opp_team":       "Opp",
    "prop_type_norm": "Prop",
    "pick_type":      "Pick_Type",
    "line":           "Line",
    "bet_direction":  "Dir",
    "tier":           "Tier",
    "def_tier":       "Def_Tier",
    "edge":           "Edge",
    "rank_score":     "Rank_Score",
    "actual":         "Actual",
    "result":         "Outcome",
    "minutes_tier":   "Minutes_Tier",
    "shot_role":      "Shot_Role",
    "usage_role":     "Usage_Role",
}

CBB_MAP = {
    "player":       "Player",
    "team":         "Team",
    "opp":          "Opp",
    "prop_label":   "Prop",
    "pick_type":    "Pick_Type",
    "line":         "Line",
    "bet_direction":"Dir",
    "tier":         "Tier",
    "opp_def_tier": "Def_Tier",
    "edge":         "Edge",
    "rank_score":   "Rank_Score",
    "actual_value": "Actual",
    "result":       "Outcome",
    "hit_rate":     "Hit_Rate",
    "prop_norm":    "Prop_Norm",
}

# Generic fallback map (for NHL/Soccer — adjust once you see their columns)
GENERIC_MAP = {
    "player":"Player","player_name":"Player",
    "team":"Team",
    "opp":"Opp","opp_team":"Opp","opponent":"Opp",
    "prop":"Prop","prop_type_norm":"Prop","prop_label":"Prop","prop_norm":"Prop","stat_type":"Prop",
    "pick_type":"Pick_Type","line_type":"Pick_Type",
    "line":"Line","line_score":"Line",
    "bet_direction":"Dir","direction":"Dir","dir":"Dir",
    "tier":"Tier","rank_tier":"Tier",
    "def_tier":"Def_Tier","opp_def_tier":"Def_Tier","defense_tier":"Def_Tier",
    "edge":"Edge","edge_adj":"Edge",
    "rank_score":"Rank_Score","score":"Rank_Score",
    "actual":"Actual","actual_value":"Actual","actual_stat":"Actual",
    "result":"Outcome","outcome":"Outcome","grade":"Outcome",
    "hit_rate":"Hit_Rate","composite_hit_rate":"Hit_Rate",
}

SPORT_MAPS = {"NBA": NBA_MAP, "CBB": CBB_MAP}

def _best_raw_sheet(xf: pd.ExcelFile) -> str:
    """Pick the sheet with per-prop rows — prefer 'Box Raw', then largest sheet."""
    for name in xf.sheet_names:
        if "box raw" in name.lower() or "raw" in name.lower():
            return name
    # fall back to largest sheet
    best, best_n = xf.sheet_names[0], 0
    for name in xf.sheet_names:
        try:
            n = len(pd.read_excel(xf, sheet_name=name, nrows=1))
            df = pd.read_excel(xf, sheet_name=name)
            if len(df) > best_n:
                best, best_n = name, len(df)
        except Exception: pass
    return best

def load_sport(path: Path, sport: str) -> pd.DataFrame:
    sport = sport.upper()
    xf = pd.ExcelFile(path)
    sheet = _best_raw_sheet(xf)
    print(f"    Reading sheet '{sheet}' from {path.name}")
    df = pd.read_excel(path, sheet_name=sheet)
    df.columns = [c.strip() for c in df.columns]

    col_map = SPORT_MAPS.get(sport, GENERIC_MAP)

    # rename known columns
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=rename)

    # for any canonical col still missing, try generic map
    for src, dst in GENERIC_MAP.items():
        if dst not in df.columns and src in df.columns:
            df[dst] = df[src]

    df["Sport"] = sport

    # normalise Outcome to uppercase HIT/MISS/VOID
    if "Outcome" in df.columns:
        df["Outcome"] = df["Outcome"].astype(str).str.strip().str.upper()
    else:
        df["Outcome"] = "VOID"

    # normalise Dir
    if "Dir" in df.columns:
        df["Dir"] = df["Dir"].astype(str).str.strip().str.upper()

    # normalise Tier — strip "Tier " prefix if present
    if "Tier" in df.columns:
        df["Tier"] = df["Tier"].astype(str).str.replace(r"(?i)^tier\s*","",regex=True).str.strip().str.upper()

    # numeric cols
    for c in ["Line","Actual","Edge","Hit_Rate","Rank_Score"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df

# ── HTML SECTION BUILDERS ──────────────────────────────────────────────────────
def stat_card(accent, label, value, sub, val_col=None):
    vc = val_col or "var(--text)"
    return (f'<div class="stat-card {accent}">'
            f'<div class="stat-label">{label}</div>'
            f'<div class="stat-val" style="color:{vc}">{value}</div>'
            f'<div class="stat-sub">{sub}</div></div>')

def bkdn_table(df, col, title):
    if col not in df.columns or df.empty: return ""
    rows = ""
    groups = sorted(df.groupby(col, dropna=True),
                    key=lambda x: -pf(*hmv(x[1])[:2]))
    for name, g in groups:
        hi,mi,vo = hmv(g); dec=hi+mi
        if   col=="Pick_Type": cell = pick_chip(name)
        elif col=="Tier":      cell = tier_chip(name)
        elif col=="Dir":
            cell = (f'<span class="pos">▲ OVER</span>' if str(name).upper()=="OVER"
                    else f'<span class="neu">▼ UNDER</span>')
        else: cell = h(name)
        rows += (f"<tr><td>{cell}</td>"
                 f'<td class="right mono">{dec}</td>'
                 f'<td class="right mono pos">{hi}</td>'
                 f'<td class="right mono neg">{mi}</td>'
                 f'<td class="right mono neu">{vo}</td>'
                 f"<td>{bar(hi,dec)}</td></tr>")
    if not rows: return ""
    return (f'<div class="section-label">{h(title)}</div>'
            f'<div class="table-wrap"><table>'
            f'<thead><tr><th>{h(col.replace("_"," "))}</th>'
            f'<th class="right">DECIDED</th><th class="right">HITS</th>'
            f'<th class="right">MISSES</th><th class="right">VOIDS</th>'
            f'<th>HIT RATE</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')

def prop_table(df):
    if "Prop" not in df.columns or df.empty: return ""
    data = []
    for name, g in df.groupby("Prop", dropna=True):
        hi,mi,vo = hmv(g)
        avg = g["Line"].mean() if "Line" in g.columns else float("nan")
        data.append((str(name), hi, mi, vo, hi+mi, avg))
    rows = ""
    for name,hi,mi,vo,dec,avg in sorted(data, key=lambda x:-x[4])[:30]:
        ls = f"{avg:.1f}" if not pd.isna(avg) else "—"
        rows += (f"<tr><td class='mono'>{h(name)}</td>"
                 f'<td class="right mono">{ls}</td>'
                 f'<td class="right mono">{dec}</td>'
                 f'<td class="right mono pos">{hi}</td>'
                 f'<td class="right mono neg">{mi}</td>'
                 f"<td>{bar(hi,dec)}</td></tr>")
    if not rows: return ""
    return (f'<div class="section-label">BY PROP TYPE</div>'
            f'<div class="table-wrap"><table>'
            f'<thead><tr><th>PROP</th><th class="right">AVG LINE</th>'
            f'<th class="right">DECIDED</th><th class="right">HITS</th>'
            f'<th class="right">MISSES</th><th>HIT RATE</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')

def player_tables(df, min_dec=3):
    if "Player" not in df.columns or df.empty: return "", ""
    data = []
    for name, g in df.groupby("Player", dropna=True):
        hi,mi,vo = hmv(g); dec=hi+mi
        if dec < min_dec: continue
        props = ", ".join(g["Prop"].dropna().unique()[:2]) if "Prop" in g.columns else ""
        data.append((str(name), hi, mi, vo, dec, props))

    def make_rows(items, top):
        out = ""; cls = "player-hit" if top else "player-miss"
        for name,hi,mi,vo,dec,props in items[:15]:
            out += (f'<tr class="{cls}"><td>'
                    f'<div style="font-weight:600">{h(name)}</div>'
                    f'<div style="font-size:11px;color:var(--muted2)">{h(props)}</div></td>'
                    f'<td class="right mono">{dec}</td>'
                    f'<td class="right mono pos">{hi}</td>'
                    f'<td class="right mono neg">{mi}</td>'
                    f'<td>{bar(hi,dec)}</td></tr>')
        return out

    thead = ('<thead><tr><th>PLAYER</th><th class="right">DEC</th>'
             '<th class="right">HITS</th><th class="right">MISS</th>'
             '<th>HIT RATE</th></tr></thead>')
    no = f'<div class="muted-note">No players with ≥{min_dec} decided props.</div>'

    top_r = make_rows(sorted(data, key=lambda x:-pf(x[1],x[4])), True)
    bot_r = make_rows(sorted(data, key=lambda x: pf(x[1],x[4])), False)

    t = f'<div class="table-wrap"><table>{thead}<tbody>{top_r}</tbody></table></div>' if top_r else no
    b = f'<div class="table-wrap"><table>{thead}<tbody>{bot_r}</tbody></table></div>' if bot_r else no
    return t, b

def legs_table(df, limit=200):
    if df.empty: return '<div class="muted-note">No leg data.</div>'
    COLS = ["Player","Prop","Dir","Pick_Type","Tier","Line","Actual","Outcome","Edge","Def_Tier"]
    avail = [c for c in COLS if c in df.columns]
    RIGHT = {"Line","Actual","Edge"}

    if "Outcome" in df.columns:
        order = {"HIT":0,"MISS":1,"VOID":2}
        df = df.copy()
        df["_s"] = df["Outcome"].astype(str).str.upper().map(order).fillna(3)
        df = df.sort_values("_s").drop(columns=["_s"])

    head = "".join(('<th class="right">' if c in RIGHT else '<th>') + h(c.replace("_"," ")) + '</th>'
                   for c in avail)
    rows = ""
    for _, row in df.head(limit).iterrows():
        oc = str(row.get("Outcome","")).upper()
        tr = "player-hit" if oc=="HIT" else ("player-miss" if oc=="MISS" else "")
        cells = ""
        for c in avail:
            v = row.get(c,"")
            if c=="Outcome":   cells += f"<td>{outcome_cell(str(v))}</td>"
            elif c=="Pick_Type": cells += f"<td>{pick_chip(str(v))}</td>"
            elif c=="Tier":    cells += f"<td>{tier_chip(str(v))}</td>"
            elif c=="Dir":
                dv = str(v).upper()
                cls2 = "pos" if dv=="OVER" else ("neu" if dv=="UNDER" else "")
                pre  = "▲ " if dv=="OVER" else ("▼ " if dv=="UNDER" else "")
                cells += f'<td><span class="{cls2}">{pre}{h(v)}</span></td>'
            elif c in RIGHT:
                try:
                    fv = float(v)
                    col2 = ""
                    if c=="Edge": col2 = f' style="color:{"var(--green)" if fv>0 else "var(--red)"}"'
                    cells += f'<td class="right mono"{col2}>{fv:.1f}</td>'
                except: cells += f'<td class="right mono neu">—</td>'
            else: cells += f"<td>{h(v)}</td>"
        rows += f'<tr class="{tr}">{cells}</tr>'

    note = (f'<div style="font-size:10px;color:var(--muted);padding:6px 14px;text-align:right">'
            f'Showing {min(limit,len(df)):,} of {len(df):,}</div>') if len(df)>limit else ""
    return (f'<div class="table-wrap" style="overflow-x:auto">'
            f'<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>{note}</div>')

def sport_section(sport, df):
    hi,mi,vo = hmv(df); total=len(df); dec=hi+mi
    color = SPORT_COLOR.get(sport,"#888"); icon = SPORT_ICON.get(sport,"🏟")
    hrp = pf(hi,dec)

    cards = (
        stat_card("green","OVERALL HIT RATE", pct(hi,dec),
                  f"{hi:,} hits / {dec:,} decided", rc(hrp)) +
        stat_card("blue","TOTAL PROPS", f"{total:,}",
                  f"<strong>{dec:,}</strong> decided &nbsp;·&nbsp; {vo:,} voids") +
        stat_card("amber","TOTAL HITS", f"{hi:,}",
                  f"{pct(hi,dec)} hit rate","var(--green)") +
        stat_card("red","TOTAL MISSES", f"{mi:,}",
                  f"{pct(mi,dec)} miss rate","var(--red)")
    )
    pick_h = bkdn_table(df,"Pick_Type","BY PICK TYPE")
    dir_h  = bkdn_table(df,"Dir",      "BY DIRECTION")
    tier_h = bkdn_table(df,"Tier",     "BY TIER")
    def_h  = bkdn_table(df,"Def_Tier", "BY DEFENSE TIER")
    prop_h = prop_table(df)
    top_h, bot_h = player_tables(df)
    legs_h = legs_table(df)

    return f"""<div class="sport-section">
  <div class="sport-header">
    <div class="sport-label" style="color:{color}">{icon} {h(sport)}</div>
    <div class="sport-header-line"></div>
    <div style="font-family:'DM Mono',monospace;font-size:11px;color:var(--muted2)">
      {total:,} TOTAL &nbsp;·&nbsp; {dec:,} DECIDED &nbsp;·&nbsp; {vo:,} VOIDS
    </div>
  </div>
  <div class="section-label">OVERALL PERFORMANCE</div>
  <div class="stat-grid stat-grid-4" style="margin-bottom:20px">{cards}</div>
  <div class="two-col"><div>{pick_h}{dir_h}</div><div>{tier_h}{def_h}</div></div>
  {prop_h}
  <div class="two-col">
    <div><div class="section-label">🏆 TOP PERFORMERS</div>{top_h}</div>
    <div><div class="section-label">💀 WORST PERFORMERS</div>{bot_h}</div>
  </div>
  <details style="margin-top:8px">
    <summary style="font-family:'DM Mono',monospace;font-size:10px;letter-spacing:2px;
      color:var(--muted);cursor:pointer;padding:10px 0;list-style:none;user-select:none">
      ▶ FULL LEGS TABLE ({total:,} props)
    </summary>
    <div style="margin-top:12px">{legs_h}</div>
  </details>
</div>"""

def combined_summary(df):
    hi,mi,vo = hmv(df); total=len(df); dec=hi+mi; hrp=pf(hi,dec)
    sport_rows = ""
    if "Sport" in df.columns:
        for sport, g in sorted(df.groupby("Sport")):
            shi,smi,svo = hmv(g); sdec=shi+smi
            color = SPORT_COLOR.get(str(sport).upper(),"#888")
            icon  = SPORT_ICON.get(str(sport).upper(),"🏟")
            sport_rows += (f'<tr><td><span style="color:{color};font-weight:700">{icon} {h(sport)}</span></td>'
                           f'<td class="right mono">{len(g):,}</td>'
                           f'<td class="right mono">{sdec:,}</td>'
                           f'<td class="right mono pos">{shi:,}</td>'
                           f'<td class="right mono neg">{smi:,}</td>'
                           f'<td class="right mono neu">{svo:,}</td>'
                           f'<td>{bar(shi,sdec)}</td></tr>')
    sport_tbl = (f'<div class="section-label">BY SPORT</div>'
                 f'<div class="table-wrap"><table>'
                 f'<thead><tr><th>SPORT</th><th class="right">TOTAL</th>'
                 f'<th class="right">DECIDED</th><th class="right">HITS</th>'
                 f'<th class="right">MISSES</th><th class="right">VOIDS</th>'
                 f'<th>HIT RATE</th></tr></thead>'
                 f'<tbody>{sport_rows}</tbody></table></div>') if sport_rows else ""
    return f"""<div class="sport-section" style="border-color:rgba(59,130,246,.25)">
  <div class="sport-header">
    <div class="sport-label" style="color:#3b82f6">📊 ALL SPORTS COMBINED</div>
    <div class="sport-header-line"></div>
    <div style="font-family:'DM Mono',monospace;font-size:11px;color:var(--muted2)">{total:,} TOTAL PROPS</div>
  </div>
  <div class="stat-grid stat-grid-4" style="margin-bottom:20px">
    {stat_card("green","COMBINED HIT RATE",pct(hi,dec),f"{hi:,} hits / {dec:,} decided",rc(hrp))}
    {stat_card("blue","TOTAL PROPS",f"{total:,}",f"{dec:,} decided props")}
    {stat_card("amber","TOTAL HITS",f"{hi:,}",f"{pct(hi,dec)} hit rate","var(--green)")}
    {stat_card("red","TOTAL MISSES",f"{mi:,}",f"{pct(mi,dec)} miss rate","var(--red)")}
  </div>
  {sport_tbl}
  <div class="two-col">
    <div>{bkdn_table(df,"Pick_Type","COMBINED PICK TYPE")}</div>
    <div>{bkdn_table(df,"Dir","COMBINED OVER / UNDER")}</div>
  </div>
</div>"""

# ── CSS ────────────────────────────────────────────────────────────────────────
CSS = """:root{--bg:#070a10;--bg2:#0c1018;--bg3:#111722;--border:#1c2333;--bd2:#243044;--text:#e8edf5;--muted:#4a5568;--muted2:#6b7a94;--blue:#3b82f6;--green:#10b981;--amber:#f59e0b;--red:#ef4444;--purple:#8b5cf6}
*{box-sizing:border-box;margin:0;padding:0}
html{overflow-x:hidden;overflow-y:scroll;scrollbar-gutter:stable;height:100%}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding-bottom:60px}
body::before{content:'';position:fixed;top:-20%;left:-10%;width:55%;height:55%;background:radial-gradient(ellipse,rgba(59,130,246,.04) 0%,transparent 70%);pointer-events:none}
body::after{content:'';position:fixed;bottom:-20%;right:-10%;width:50%;height:50%;background:radial-gradient(ellipse,rgba(16,185,129,.03) 0%,transparent 70%);pointer-events:none}
header{background:rgba(7,10,16,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:18px 32px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.logo{display:flex;align-items:center;gap:14px}
.logo-icon{width:186px;height:186px;object-fit:contain;display:block;filter:drop-shadow(0 0 6px rgba(212,175,55,0.45))}
.logo-title{font-family:'Bebas Neue',sans-serif;font-size:26px;letter-spacing:2px;background:linear-gradient(135deg,#fff 40%,#94a3b8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo-sub{font-family:'DM Mono',monospace;font-size:10px;color:var(--muted);letter-spacing:2.5px;margin-top:2px}
.date-badge{font-family:'DM Mono',monospace;font-size:11px;color:var(--muted2);background:var(--bg3);border:1px solid var(--bd2);border-radius:8px;padding:6px 14px;letter-spacing:1px}
.main{max-width:1200px;margin:0 auto;padding:28px 20px}
.sport-header{display:flex;align-items:center;gap:14px;margin-bottom:22px}
.sport-label{font-family:'Bebas Neue',sans-serif;font-size:32px;letter-spacing:3px;line-height:1}
.sport-header-line{flex:1;height:1px;background:linear-gradient(90deg,var(--bd2),transparent)}
.sport-section{margin-bottom:32px;background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:24px}
.section-label{font-family:'DM Mono',monospace;font-size:10px;color:var(--muted);letter-spacing:3px;display:flex;align-items:center;gap:10px;margin-bottom:14px;margin-top:6px}
.section-label::after{content:'';flex:1;height:1px;background:var(--border)}
.stat-grid{display:grid;gap:14px;margin-bottom:20px}
.stat-grid-4{grid-template-columns:repeat(4,1fr)}
.stat-card{background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:16px 18px;position:relative;overflow:hidden}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.stat-card.green::before{background:linear-gradient(90deg,var(--green),transparent)}
.stat-card.blue::before{background:linear-gradient(90deg,var(--blue),transparent)}
.stat-card.amber::before{background:linear-gradient(90deg,var(--amber),transparent)}
.stat-card.red::before{background:linear-gradient(90deg,var(--red),transparent)}
.stat-label{font-family:'DM Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:2.5px;margin-bottom:8px}
.stat-val{font-family:'Bebas Neue',sans-serif;font-size:36px;letter-spacing:1px;line-height:1}
.stat-sub{font-size:12px;color:var(--muted2);margin-top:5px}
.stat-sub strong{font-weight:700}
.table-wrap{background:var(--bg3);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-family:'DM Mono',monospace;font-size:9px;letter-spacing:2px;color:var(--muted);padding:10px 14px;text-align:left;background:rgba(0,0,0,.3);border-bottom:1px solid var(--border);white-space:nowrap}
th.right{text-align:right}
td{padding:9px 14px;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.015)}
td.right{text-align:right}
td.mono{font-family:'DM Mono',monospace;font-size:12px}
.rc{display:flex;align-items:center;gap:10px}
.rb{flex:1;height:5px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden;min-width:50px}
.rf{height:100%;border-radius:3px}
.rn{font-family:'DM Mono',monospace;font-size:12px;width:44px;text-align:right;flex-shrink:0}
.chip{display:inline-block;border-radius:6px;padding:2px 9px;font-size:11px;font-weight:700;font-family:'DM Mono',monospace;letter-spacing:.5px}
.chip-a{background:rgba(16,185,129,.12);color:#6ee7b7;border:1px solid rgba(16,185,129,.25)}
.chip-b{background:rgba(59,130,246,.12);color:#93c5fd;border:1px solid rgba(59,130,246,.25)}
.chip-c{background:rgba(245,158,11,.12);color:#fcd34d;border:1px solid rgba(245,158,11,.25)}
.chip-d{background:rgba(100,116,139,.12);color:#94a3b8;border:1px solid rgba(100,116,139,.25)}
.chip-goblin{background:rgba(139,92,246,.15);color:#c4b5fd;border:1px solid rgba(139,92,246,.3)}
.chip-demon{background:rgba(239,68,68,.12);color:#fca5a5;border:1px solid rgba(239,68,68,.25)}
.chip-std{background:rgba(59,130,246,.12);color:#93c5fd;border:1px solid rgba(59,130,246,.25)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.player-hit td:first-child{border-left:3px solid var(--green)}
.player-miss td:first-child{border-left:3px solid var(--red)}
.pos{color:var(--green);font-weight:700}.neg{color:var(--red);font-weight:700}.neu{color:var(--muted2)}
.muted-note{font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);padding:16px;text-align:center}
details summary::-webkit-details-marker{display:none}
.right{text-align:right}.mono{font-family:'DM Mono',monospace;font-size:12px}
.footer{font-family:'DM Mono',monospace;font-size:10px;color:var(--muted);text-align:center;margin-top:32px;letter-spacing:1.5px;padding-bottom:20px}
@media(max-width:800px){.stat-grid-4{grid-template-columns:repeat(2,1fr)}.two-col{grid-template-columns:1fr}.sport-section{padding:16px}}"""

# ── BUILD HTML ─────────────────────────────────────────────────────────────────
def build_html(df: pd.DataFrame, date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        display_date = d.strftime("%b %d, %Y").upper()
    except ValueError:
        display_date = date_str.upper()

    hi,mi,vo = hmv(df); total=len(df); dec=hi+mi
    summary = combined_summary(df)

    sections = ""
    if "Sport" in df.columns:
        for sport in ["NBA","CBB","NHL","SOCCER","MLB"]:
            sdf = df[df["Sport"]==sport]
            if not sdf.empty: sections += sport_section(sport, sdf)
        known = {"NBA","CBB","NHL","SOCCER","MLB"}
        for sport, sdf in df.groupby("Sport"):
            if str(sport).upper() not in known and not sdf.empty:
                sections += sport_section(str(sport).upper(), sdf)
    else:
        sections = sport_section("ALL", df)

    gen = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Slate Eval — {h(display_date)}</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="/static/global-scrollbar.css?v=20260517platform"/>
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
  <div class="date-badge">📅 {h(display_date)} &nbsp;·&nbsp; {total:,} props &nbsp;·&nbsp; {hi:,} hits &nbsp;·&nbsp; {mi:,} misses &nbsp;·&nbsp; {vo:,} voids</div>
</header>
<div class="main">
  {summary}
  {sections}
  <div class="footer">PROPORACLE · GENERATED {gen}</div>
</div>
</body>
</html>"""

# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date");   ap.add_argument("--nba")
    ap.add_argument("--cbb");   ap.add_argument("--nhl")
    ap.add_argument("--soccer"); ap.add_argument("--mlb")
    ap.add_argument("--out")
    args = ap.parse_args()

    date_str = args.date or (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"  Date: {date_str}")

    sport_args = {"NBA":args.nba,"CBB":args.cbb,"NHL":args.nhl,
                  "SOCCER":args.soccer,"MLB":args.mlb}
    frames = []
    for sport, path_str in sport_args.items():
        if not path_str: continue
        p = Path(path_str)
        if not p.is_absolute(): p = BASE_DIR / p
        if not p.exists():
            print(f"  WARNING: {sport} file not found: {p}"); continue
        print(f"  Loading {sport}: {p.name}")
        try:
            df = load_sport(p, sport)
            print(f"    {len(df):,} rows — Outcome values: {df['Outcome'].value_counts().to_dict()}")
            frames.append(df)
        except Exception as e:
            print(f"  ERROR loading {sport}: {e}")

    # auto-detect if nothing passed
    if not frames:
        for sport, pat in [("NBA","graded_nba_%s.xlsx"),("CBB","graded_cbb_%s.xlsx"),
                           ("NHL","graded_nhl_%s.xlsx"),("SOCCER","graded_soccer_%s.xlsx")]:
            for folder in [BASE_DIR/"outputs"/date_str, BASE_DIR/"outputs"]:
                p = folder / (pat % date_str)
                if p.exists():
                    print(f"  Auto: {p.name}")
                    frames.append(load_sport(p, sport)); break

    if not frames:
        print(f"ERROR: no graded files found for {date_str}"); sys.exit(1)

    df = pd.concat(frames, ignore_index=True)
    hi,mi,vo = hmv(df)
    print(f"  Combined: {len(df):,} rows — HIT:{hi} MISS:{mi} VOID:{vo}")

    html = build_html(df, date_str)
    out = Path(args.out) if args.out else TEMPLATES_DIR / f"slate_eval_{date_str}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"  Saved → {out}  ({len(html):,} bytes)")

if __name__ == "__main__":
    main()
