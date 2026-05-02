#!/usr/bin/env python3
"""
generate_intel_dashboard.py
PropOracle Intelligence Dashboard Generator

Reads directly from proporacle_ref.db to generate a live HTML dashboard showing:
  - Player consistency (Season Avg, L5, L10, CV%)
  - Team defense profiles (which teams give up which props)
  - H2H history (player vs specific opponent)
  - Trending players (L5 vs season)

Run:
  py -3.14 scripts/generate_intel_dashboard.py
  py -3.14 scripts/generate_intel_dashboard.py --out data/outputs/intel_dashboard.html
  py -3.14 scripts/generate_intel_dashboard.py --prop points --min-gp 15
"""

from __future__ import annotations
import argparse, sqlite3, unicodedata, sys
from pathlib import Path
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── DB path resolution ────────────────────────────────────────────────────────
_here = Path(__file__).resolve().parent
DB_PATH = None
for _ in range(6):
    candidate = _here.parent / "data" / "cache" / "proporacle_ref.db"
    if candidate.exists():
        DB_PATH = candidate
        break
    _here = _here.parent

# ── Prop → DB column ─────────────────────────────────────────────────────────
PROPS = {
    "points":    ("pts",           "Points"),
    "rebounds":  ("reb",           "Rebounds"),
    "assists":   ("ast",           "Assists"),
    "steals":    ("stl",           "Steals"),
    "blocks":    ("blk",           "Blocks"),
    "3pm":       ("fg3m",          "3PM"),
    "ftm":       ("ftm",           "FTM"),
    "fantasy":   ("fantasy_score", "Fantasy"),
    "pra":       ("pra",           "PRA"),
    "pr":        ("pr",            "Pts+Reb"),
    "pa":        ("pa",            "Pts+Ast"),
}

def _norm(n):
    s = unicodedata.normalize("NFD", str(n).strip().lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def load_all_players(con, db_col, min_gp=10):
    """Load all player season stats for a given stat column."""
    rows = con.execute(f"""
        SELECT player, team, game_date, {db_col}
        FROM nba
        WHERE {db_col} IS NOT NULL
        ORDER BY player, game_date DESC
    """).fetchall()

    # Group by player
    from collections import defaultdict
    player_games = defaultdict(list)
    player_team  = {}
    for player, team, date, val in rows:
        player_games[player].append(float(val))
        player_team[player] = team

    results = []
    for player, vals in player_games.items():
        if len(vals) < min_gp:
            continue
        arr = np.array(vals)
        n   = len(arr)
        avg = float(np.mean(arr))
        std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        cv  = round(std / avg * 100, 1) if avg > 0 else 999.0
        l5  = float(np.mean(arr[:5]))
        l10 = float(np.mean(arr[:10]))
        results.append({
            "player": player,
            "team":   player_team[player],
            "gp":     n,
            "season_avg": round(avg, 1),
            "l5_avg":     round(l5, 1),
            "l10_avg":    round(l10, 1),
            "cv_pct":     cv,
            "l5_vs_season": round(l5 - avg, 1),
            "last_3": [round(v, 1) for v in arr[:3]],
        })

    return sorted(results, key=lambda x: x["cv_pct"])

def load_defense(con, db_col):
    """Load team defense profile for a stat column."""
    lg_avg = con.execute(
        f"SELECT AVG({db_col}) FROM nba WHERE {db_col} IS NOT NULL"
    ).fetchone()[0]
    if not lg_avg:
        return []

    teams = con.execute(
        "SELECT DISTINCT home_team FROM nba"
    ).fetchall()
    team_list = [r[0] for r in teams if r[0]]

    results = []
    for team in team_list:
        vals = con.execute(f"""
            SELECT {db_col} FROM nba
            WHERE (upper(team) != upper(?) 
              AND (upper(home_team) = upper(?) OR upper(away_team) = upper(?)))
              AND {db_col} IS NOT NULL
        """, [team, team, team]).fetchall()
        if len(vals) < 20:
            continue
        avg = float(np.mean([v[0] for v in vals]))
        results.append({
            "team":      team,
            "avg":       round(avg, 2),
            "league":    round(float(lg_avg), 2),
            "vs_pct":    round((avg / float(lg_avg) - 1) * 100, 1),
            "n":         len(vals),
        })

    return sorted(results, key=lambda x: -x["vs_pct"])

def load_h2h(con, db_col, min_games=2):
    """Load H2H stats for all player-opponent combos."""
    rows = con.execute(f"""
        SELECT n.player, n.team, 
               CASE WHEN upper(n.team) = upper(n.away_team) 
                    THEN n.home_team ELSE n.away_team END as opp,
               n.{db_col}
        FROM nba n
        WHERE n.{db_col} IS NOT NULL
        ORDER BY n.player, n.game_date DESC
    """).fetchall()

    from collections import defaultdict
    combos = defaultdict(list)
    for player, team, opp, val in rows:
        combos[(player, team, opp)].append(float(val))

    results = []
    for (player, team, opp), vals in combos.items():
        if len(vals) < min_games:
            continue
        avg = float(np.mean(vals))
        results.append({
            "player":  player,
            "team":    team,
            "opp":     opp,
            "games":   len(vals),
            "avg":     round(avg, 1),
            "last":    round(vals[0], 1),
        })

    return sorted(results, key=lambda x: -x["avg"])[:200]


def build_html(prop_key, con, min_gp, db_date_range):
    db_col, prop_label = PROPS[prop_key]

    print(f"  Loading player consistency...")
    players = load_all_players(con, db_col, min_gp)
    print(f"  Loading team defense...")
    defense = load_defense(con, db_col)
    print(f"  Loading H2H...")
    h2h     = load_h2h(con, db_col)

    # Trending
    trending_up   = sorted([p for p in players if p["gp"] >= 10],
                           key=lambda x: -x["l5_vs_season"])[:25]
    trending_down = sorted([p for p in players if p["gp"] >= 10],
                           key=lambda x: x["l5_vs_season"])[:25]

    def pct_color(v):
        if v >= 5:   return "#34d399"
        if v >= 0:   return "#fbbf24"
        if v >= -5:  return "#f97316"
        return "#f87171"

    def trend_color(v):
        return "#34d399" if v > 0.5 else "#f87171" if v < -0.5 else "#94a3b8"

    # Build prop tabs
    prop_tabs = "".join(
        f'<button class="ptab{" active" if k == prop_key else ""}" '
        f'onclick="switchProp(\'{k}\')">{PROPS[k][1]}</button>'
        for k in PROPS
    )

    # Build consistency rows
    cons_rows = "".join(f"""
        <tr>
          <td><b>{p["player"]}</b></td>
          <td class="muted">{p["team"]}</td>
          <td>{p["gp"]}</td>
          <td><b>{p["season_avg"]}</b></td>
          <td>{p["l5_avg"]}</td>
          <td>{p["l10_avg"]}</td>
          <td style="color:{trend_color(p["l5_vs_season"])};font-weight:500">
              {("+" if p["l5_vs_season"] > 0 else "") + str(p["l5_vs_season"])}</td>
          <td><span class="badge {'green' if p["cv_pct"] < 35 else 'red' if p["cv_pct"] > 65 else 'amber'}">{p["cv_pct"]}%</span></td>
          <td class="muted">{" / ".join(str(v) for v in p["last_3"])}</td>
        </tr>""" for p in players[:60])

    # Defense rows
    def_rows = "".join(f"""
        <tr>
          <td><b>{d["team"]}</b></td>
          <td>{d["avg"]}</td>
          <td class="muted">{d["league"]}</td>
          <td><span class="badge {'green' if d["vs_pct"] > 5 else 'red' if d["vs_pct"] < -5 else 'amber'}">{("+" if d["vs_pct"] > 0 else "") + str(d["vs_pct"])}%</span></td>
          <td class="muted">{d["n"]} player-games</td>
        </tr>""" for d in defense)

    # H2H rows
    h2h_rows = "".join(f"""
        <tr>
          <td><b>{r["player"]}</b></td>
          <td class="muted">{r["team"]}</td>
          <td><b>{r["opp"]}</b></td>
          <td>{r["games"]}</td>
          <td><b>{r["avg"]}</b></td>
          <td style="color:{trend_color(r["last"] - r["avg"])}">{r["last"]}</td>
        </tr>""" for r in h2h[:80])

    # Trending rows
    def trend_row(p, up):
        return f"""<tr>
          <td><b>{p["player"]}</b></td>
          <td class="muted">{p["team"]}</td>
          <td>{p["season_avg"]}</td>
          <td><b>{p["l5_avg"]}</b></td>
          <td style="color:{"#34d399" if up else "#f87171"};font-weight:500">
              {("+" if p["l5_vs_season"] > 0 else "") + str(p["l5_vs_season"])}</td>
          <td class="muted">{p["l10_avg"]}</td>
        </tr>"""

    trend_up_rows   = "".join(trend_row(p, True)  for p in trending_up)
    trend_down_rows = "".join(trend_row(p, False) for p in trending_down)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>PropOracle Intelligence — {prop_label}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#070a10;color:#e2e8f0;padding:20px;font-size:13px}}
h1{{color:#a78bfa;font-size:20px;margin-bottom:4px}}
.sub{{color:#475569;font-size:12px;margin-bottom:16px}}
.tabs{{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}}
.tab{{cursor:pointer;padding:6px 14px;border-radius:6px;font-size:12px;
      border:1px solid #1e293b;background:transparent;color:#64748b}}
.tab.active,.tab:hover{{background:#4c1d95;color:#ddd6fe;border-color:#7c3aed}}
.prop-tabs{{display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap}}
.ptab{{cursor:pointer;padding:4px 10px;border-radius:10px;font-size:11px;
       border:1px solid #1e293b;color:#64748b;background:transparent}}
.ptab.active{{background:#065f46;color:#34d399;border-color:#34d399}}
.panel{{display:none}}.panel.active{{display:block}}
input{{width:100%;padding:7px 12px;border-radius:6px;border:1px solid #1e293b;
       background:#0f172a;color:#e2e8f0;font-size:12px;margin-bottom:10px;outline:none}}
table{{width:100%;border-collapse:collapse}}
th{{color:#64748b;padding:6px 8px;text-align:left;border-bottom:1px solid #1e293b;
    font-size:11px;font-weight:500;white-space:nowrap}}
td{{padding:6px 8px;border-bottom:1px solid #0f172a;white-space:nowrap}}
tr:hover td{{background:#0f172a}}
.muted{{color:#475569}}
.badge{{display:inline-block;padding:1px 7px;border-radius:8px;font-size:11px;font-weight:600}}
.green{{background:#064e3b;color:#34d399}}
.amber{{background:#451a03;color:#fbbf24}}
.red{{background:#450a0a;color:#f87171}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px}}
.card{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:14px}}
.card h3{{color:#7c3aed;font-size:13px;margin-bottom:10px}}
.trend-label{{font-size:11px;font-weight:500;margin-bottom:6px}}
.up{{color:#34d399}}.down{{color:#f87171}}
</style></head>
<body>
<h1>PropOracle Intelligence Dashboard</h1>
<div class="sub">DB: {db_date_range} · Live from proporacle_ref.db · Auto-updates nightly</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('consistency',this)">📊 Player Consistency</button>
  <button class="tab" onclick="showTab('defense',this)">🛡️ Team Defense</button>
  <button class="tab" onclick="showTab('h2h',this)">🔁 H2H History</button>
  <button class="tab" onclick="showTab('trending',this)">📈 Trending</button>
</div>

<div class="prop-tabs">{prop_tabs}</div>
<div id="prop-label" style="font-size:11px;color:#475569;margin-bottom:10px">
  Showing: <b style="color:#a78bfa">{prop_label}</b> · Reload page after switching prop
</div>

<!-- CONSISTENCY -->
<div class="panel active" id="panel-consistency">
  <input type="text" placeholder="Filter player..." oninput="filterTable('cons-tbody',this.value)">
  <table>
    <thead><tr>
      <th>Player</th><th>Team</th><th>GP</th><th>Season Avg</th>
      <th>L5 Avg</th><th>L10 Avg</th><th>L5 vs Season</th>
      <th>CV% ↑ (lower=consistent)</th><th>Last 3 Games</th>
    </tr></thead>
    <tbody id="cons-tbody">{cons_rows}</tbody>
  </table>
</div>

<!-- DEFENSE -->
<div class="panel" id="panel-defense">
  <p style="color:#475569;font-size:12px;margin-bottom:10px">
    Green = gives up MORE than league avg (target for OVER). Red = gives up LESS (avoid or fade).
    Per player-game average.
  </p>
  <table>
    <thead><tr>
      <th>Opp Team</th><th>Avg Allowed / Player</th><th>League Avg</th>
      <th>vs League</th><th>Sample</th>
    </tr></thead>
    <tbody>{def_rows}</tbody>
  </table>
</div>

<!-- H2H -->
<div class="panel" id="panel-h2h">
  <input type="text" placeholder="Filter player or opponent..." oninput="filterTable('h2h-tbody',this.value)">
  <table>
    <thead><tr>
      <th>Player</th><th>Team</th><th>Opponent</th>
      <th>H2H Games</th><th>H2H Avg</th><th>Last Game</th>
    </tr></thead>
    <tbody id="h2h-tbody">{h2h_rows}</tbody>
  </table>
</div>

<!-- TRENDING -->
<div class="panel" id="panel-trending">
  <div class="grid">
    <div class="card">
      <h3>▲ Trending Up — L5 above season avg</h3>
      <table>
        <thead><tr><th>Player</th><th>Team</th><th>Season</th>
          <th>L5</th><th>Δ</th><th>L10</th></tr></thead>
        <tbody>{trend_up_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h3>▼ Trending Down — L5 below season avg</h3>
      <table>
        <thead><tr><th>Player</th><th>Team</th><th>Season</th>
          <th>L5</th><th>Δ</th><th>L10</th></tr></thead>
        <tbody>{trend_down_rows}</tbody>
      </table>
    </div>
  </div>
</div>

<script>
function showTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
  document.getElementById('panel-'+id).classList.add('active');
  btn.classList.add('active');
}}
function filterTable(tbodyId, q) {{
  const rows = document.getElementById(tbodyId).rows;
  q = q.toLowerCase();
  for (let r of rows) {{
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
  }}
}}
function switchProp(k) {{
  const url = new URL(window.location);
  url.searchParams.set('prop', k);
  window.location = url;
}}
// On load, restore active prop tab
const urlProp = new URLSearchParams(window.location.search).get('prop') || '{prop_key}';
document.querySelectorAll('.ptab').forEach(b => {{
  if (b.getAttribute('onclick').includes("'"+urlProp+"'")) b.classList.add('active');
  else b.classList.remove('active');
}});
</script>
</body></html>"""

    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prop",   default="points",
                    choices=list(PROPS.keys()),
                    help="Prop to analyse (default: points)")
    ap.add_argument("--min-gp", type=int, default=10,
                    help="Minimum games played for consistency table")
    ap.add_argument("--out",    default="data/outputs/intel_dashboard.html",
                    help="Output HTML path")
    ap.add_argument("--db",     default="", help="Override DB path")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path or not db_path.exists():
        print(f"❌ DB not found. Run: py scripts/build_boxscore_ref.py --backfill --days 150")
        return

    con = sqlite3.connect(str(db_path))
    db_min = con.execute("SELECT MIN(game_date) FROM nba").fetchone()[0]
    db_max = con.execute("SELECT MAX(game_date) FROM nba").fetchone()[0]
    db_n   = con.execute("SELECT COUNT(*) FROM nba").fetchone()[0]
    db_range = f"{db_min} → {db_max} · {db_n:,} rows"
    print(f"[Intel] DB: {db_range}")
    print(f"[Intel] Building dashboard for prop: {args.prop}")

    html = build_html(args.prop, con, args.min_gp, db_range)
    con.close()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ Dashboard saved → {out_path}")
    print(f"   Open in browser: file:///{out_path.resolve()}")


if __name__ == "__main__":
    main()
