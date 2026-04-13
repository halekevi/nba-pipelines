"""
build_ticket_eval_html.py
==========================
Converts combined_tickets_graded_*.xlsx into an HTML eval report
showing ticket performance, leg results, and stats.

Usage:
    py -3.14 build_ticket_eval_html.py --date 2026-03-07 --graded path/to/graded.xlsx --out output.html
"""

from __future__ import annotations

import argparse
import html as html_lib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas not installed. Run: pip install pandas")
    sys.exit(1)


def h(v: Any) -> str:
    """HTML escape."""
    return html_lib.escape(str(v) if v is not None else "")


def fmt(v: Any, dec: int = 2) -> str:
    """Format number."""
    try:
        return f"{float(v):.{dec}f}"
    except (TypeError, ValueError):
        return str(v) if v is not None else "—"


def pct(v: Any) -> str:
    """Format percentage."""
    try:
        f = float(v)
        return f"{f*100:.1f}%" if f <= 1.0 else f"{f:.1f}%"
    except (TypeError, ValueError):
        return "—"


def outcome_class(outcome: str) -> str:
    """CSS class for outcome."""
    if outcome == "HIT":
        return "outcome-hit"
    elif outcome == "MISS":
        return "outcome-miss"
    elif outcome == "PUSH":
        return "outcome-push"
    else:
        return "outcome-void"


def outcome_badge(outcome: str) -> str:
    """HTML badge for outcome."""
    labels = {
        "HIT": "✓ HIT",
        "MISS": "✗ MISS",
        "PUSH": "↔ PUSH",
        "NO_ACTUAL": "⊘ NO ACTUAL",
        "VOID": "∅ VOID",
    }
    label = labels.get(outcome, str(outcome))
    cls = outcome_class(outcome)
    return f'<span class="badge {cls}">{label}</span>'


CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --glass: rgba(255, 255, 255, 0.045);
  --glass-bd: rgba(255, 255, 255, 0.1);
  --gold: #d4af37;
  --gold2: #f0a500;
  --cyan: #7fc7d9;
  --accent: #00e5ff;
  --green: #39ff6e;
  --red: #ff4d4d;
  --amber: #fcd34d;
  --text: rgba(232, 236, 255, 0.95);
  --muted: rgba(255, 255, 255, 0.52);
  --slate-400: #94a3b8;
}

body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: linear-gradient(180deg, #05050f 0%, #0a0a18 45%, #080814 100%);
  color: var(--text);
  line-height: 1.5;
  overflow-x: hidden;
  min-height: 100vh;
}

body::before {
  content: '';
  position: fixed;
  top: -20%;
  left: -10%;
  width: 55%;
  height: 55%;
  background: radial-gradient(ellipse, rgba(212, 160, 23, 0.07) 0%, transparent 70%);
  pointer-events: none;
  z-index: 0;
}

body::after {
  content: '';
  position: fixed;
  bottom: -20%;
  right: -10%;
  width: 50%;
  height: 50%;
  background: radial-gradient(ellipse, rgba(0, 229, 255, 0.06) 0%, transparent 70%);
  pointer-events: none;
  z-index: 0;
}

header, .main { position: relative; z-index: 1; }

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

header {
  position: relative;
  text-align: center;
  padding: 22px 20px;
  background: rgba(255, 255, 255, 0.055);
  backdrop-filter: blur(28px) saturate(185%);
  -webkit-backdrop-filter: blur(28px) saturate(185%);
  border-bottom: 1px solid rgba(212, 175, 55, 0.22);
  border-radius: 0 0 22px 22px;
  box-shadow:
    0 12px 40px rgba(0, 0, 0, 0.38),
    inset 0 1px 0 rgba(255, 255, 255, 0.07);
}

.logo-title {
  font-family: 'Bebas Neue', sans-serif;
  font-size: clamp(26px, 4vw, 34px);
  letter-spacing: 0.14em;
  background: linear-gradient(to bottom, #f0a500, #d4af37, #f7e08a);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  -webkit-text-fill-color: transparent;
  filter: drop-shadow(0 0 20px rgba(212, 175, 55, 0.2));
}

.logo-sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.58);
  margin-top: 6px;
  text-transform: uppercase;
  letter-spacing: 0.2em;
}

.date-badge {
  position: absolute;
  top: 20px;
  right: 20px;
  padding: 8px 14px;
  font-size: 11px;
  letter-spacing: 0.12em;
  color: rgba(255, 255, 255, 0.75);
  background: rgba(255, 255, 255, 0.06);
  backdrop-filter: blur(16px) saturate(160%);
  -webkit-backdrop-filter: blur(16px) saturate(160%);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 999px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.22);
}

.main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px 20px 32px;
}

.section {
  margin-bottom: 28px;
}

.section-title {
  font-family: 'Bebas Neue', sans-serif;
  font-size: clamp(20px, 2.4vw, 26px);
  font-weight: 400;
  color: var(--cyan);
  margin-bottom: 14px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  padding-bottom: 10px;
  text-shadow: 0 0 22px rgba(0, 229, 255, 0.12);
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}

.kpi-card {
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(22px) saturate(165%);
  -webkit-backdrop-filter: blur(22px) saturate(165%);
  border: 1px solid rgba(212, 175, 55, 0.14);
  border-radius: 16px;
  padding: 14px 12px;
  text-align: center;
  box-shadow:
    0 8px 28px rgba(0, 0, 0, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
  transition: border-color 0.2s, transform 0.2s, box-shadow 0.2s;
}

.kpi-card:hover {
  border-color: rgba(212, 175, 55, 0.28);
  transform: translateY(-2px);
  box-shadow:
    0 12px 36px rgba(0, 0, 0, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

.kpi-val {
  font-size: clamp(18px, 2.2vw, 22px);
  font-weight: 700;
  color: var(--gold);
  margin-bottom: 6px;
  text-shadow: 0 0 20px rgba(212, 175, 55, 0.28);
}

.kpi-label {
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.table-wrapper {
  overflow-x: auto;
  background: rgba(255, 255, 255, 0.045);
  backdrop-filter: blur(24px) saturate(175%);
  -webkit-backdrop-filter: blur(24px) saturate(175%);
  border: 1px solid var(--glass-bd);
  border-radius: 18px;
  margin-bottom: 20px;
  box-shadow:
    0 10px 36px rgba(0, 0, 0, 0.34),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

th {
  background: rgba(4, 6, 16, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  padding: 10px 12px;
  text-align: left;
  font-weight: 700;
  color: var(--cyan);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

td {
  padding: 8px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

tr:last-child td {
  border-bottom: none;
}

tbody tr:hover td {
  background: rgba(212, 175, 55, 0.07);
}

.badge {
  display: inline-block;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  backdrop-filter: blur(8px);
}

.outcome-hit {
  background: rgba(57, 255, 110, 0.12);
  color: var(--green);
  border: 1px solid rgba(57, 255, 110, 0.35);
}

.outcome-miss {
  background: rgba(255, 77, 77, 0.12);
  color: var(--red);
  border: 1px solid rgba(255, 77, 77, 0.35);
}

.outcome-push {
  background: rgba(252, 211, 77, 0.12);
  color: var(--amber);
  border: 1px solid rgba(252, 211, 77, 0.3);
}

.outcome-void {
  background: rgba(148, 163, 184, 0.1);
  color: var(--slate-400);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.chip {
  display: inline-block;
  padding: 3px 7px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 500;
}

.chip-a { background: rgba(110, 231, 183, 0.2); color: #6ee7b7; }
.chip-b { background: rgba(0, 217, 255, 0.2); color: var(--accent); }
.chip-c { background: rgba(252, 211, 77, 0.2); color: #fcd34d; }
.chip-d { background: rgba(148, 163, 184, 0.2); color: var(--slate-400); }

.num { font-family: 'Courier New', monospace; }

.footer {
  text-align: center;
  padding: 20px;
  color: rgba(255, 255, 255, 0.45);
  font-size: 11px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  margin-top: 40px;
}

.ticket-row {
  cursor: pointer;
  user-select: none;
}

.ticket-row.winner {
  background: rgba(57, 255, 110, 0.06);
}

.ticket-row.winner:hover {
  background: rgba(57, 255, 110, 0.12);
}

.profit {
  font-weight: 600;
}

.profit.positive {
  color: var(--green);
}

.profit.negative {
  color: var(--red);
}

.profit.breakeven {
  color: var(--slate-400);
}
"""


def build_html(graded_path: Path) -> str:
    """Build HTML from graded tickets workbook."""
    
    # Read sheets
    try:
        summary_df = pd.read_excel(graded_path, sheet_name="SUMMARY")
        tickets_df = pd.read_excel(graded_path, sheet_name="TICKET_RESULTS")
        legs_df = pd.read_excel(graded_path, sheet_name="LEG_RESULTS")
    except Exception as e:
        print(f"ERROR reading {graded_path}: {e}")
        return ""
    
    if tickets_df.empty:
        print("WARNING: No graded ticket data found — HTML not written.")
        return ""
    
    # Extract summary metrics
    summary_dict = dict(zip(summary_df["metric"], summary_df["value"]))
    
    power_tickets = int(summary_dict.get("power_tickets", 0))
    power_eligible = int(summary_dict.get("power_eligible_tickets", 0))
    power_no_actual = int(summary_dict.get("power_no_actual_tickets", 0))
    
    flex_tickets = int(summary_dict.get("flex_tickets", 0))
    flex_eligible = int(summary_dict.get("flex_eligible_tickets", 0))
    flex_no_actual = int(summary_dict.get("flex_no_actual_tickets", 0))
    
    # Overall stats
    total_tickets = len(tickets_df)
    winners = len(tickets_df[tickets_df["is_win"] == 1])
    cashers = len(tickets_df[tickets_df["is_cash"] == 1])
    total_staked = tickets_df["stake"].sum()
    total_payout = tickets_df["payout"].sum()
    total_profit = tickets_df["profit"].sum()
    
    win_rate = winners / total_tickets if total_tickets > 0 else 0
    roi = total_profit / total_staked if total_staked > 0 else 0
    
    # Date
    display_date = datetime.now().strftime("%b %d, %Y").upper()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # KPI section
    kpi_html = f"""
    <div class="section">
      <div class="section-title">📊 Overview</div>
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-val">{total_tickets}</div>
          <div class="kpi-label">Total Tickets</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">{winners}</div>
          <div class="kpi-label">Winners</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">{pct(win_rate)}</div>
          <div class="kpi-label">Win Rate</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">${fmt(total_profit)}</div>
          <div class="kpi-label">Net Profit</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">{pct(roi)}</div>
          <div class="kpi-label">ROI</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-val">${fmt(total_staked)}</div>
          <div class="kpi-label">Total Staked</div>
        </div>
      </div>
    </div>
    """
    
    # Ticket results table
    tickets_rows = []
    for _, row in tickets_df.iterrows():
        is_win = row.get("is_win", 0) == 1
        css_class = "ticket-row winner" if is_win else "ticket-row"
        
        profit = row.get("profit")
        if pd.isna(profit):
            profit_html = "—"
            profit_class = ""
        else:
            profit_val = float(profit)
            if profit_val > 0:
                profit_class = "positive"
            elif profit_val < 0:
                profit_class = "negative"
            else:
                profit_class = "breakeven"
            profit_html = f"<span class='profit {profit_class}'>${fmt(profit_val)}</span>"
        
        stake = row.get("stake", 0)
        payout = row.get("payout")
        if pd.isna(payout):
            payout_html = "—"
        else:
            payout_html = f"${fmt(payout)}"
        
        legs = row.get("legs", 0)
        hits = row.get("hits", 0)
        misses = row.get("misses", 0)
        no_actual = row.get("no_actual", 0)
        
        tickets_rows.append(f"""
        <tr class="{css_class}">
          <td>{h(row.get("sheet", "—"))}</td>
          <td class="num">{int(row.get("ticket_no", 0))}</td>
          <td class="num">{legs}</td>
          <td class="num">{hits}/{misses}/{no_actual}</td>
          <td class="num">${fmt(stake)}</td>
          <td>{payout_html}</td>
          <td>{profit_html}</td>
          <td>{outcome_badge(row.get("payout_status", "VOID"))}</td>
        </tr>
        """)
    
    tickets_html = f"""
    <div class="section">
      <div class="section-title">🎫 Ticket Results</div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Sheet</th>
              <th>#</th>
              <th>Legs</th>
              <th>H/M/N</th>
              <th>Stake</th>
              <th>Payout</th>
              <th>Profit</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {"".join(tickets_rows)}
          </tbody>
        </table>
      </div>
    </div>
    """
    
    # Leg results - sample (top 20)
    leg_rows = []
    for _, row in legs_df.head(20).iterrows():
        leg_rows.append(f"""
        <tr>
          <td>{h(row.get("sheet", "—"))}</td>
          <td class="num">{int(row.get("ticket_no", 0))}</td>
          <td class="num">{int(row.get("leg_no", 0))}</td>
          <td>{h(row.get("player", "—"))}</td>
          <td>{h(row.get("prop_norm", "—"))}</td>
          <td>{h(row.get("dir", "—"))}</td>
          <td class="num">{fmt(row.get("line"), 1)}</td>
          <td class="num">{fmt(row.get("actual"), 1)}</td>
          <td>{outcome_badge(row.get("leg_result", "VOID"))}</td>
        </tr>
        """)
    
    legs_html = f"""
    <div class="section">
      <div class="section-title">🦵 Leg Details (Sample)</div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Sheet</th>
              <th>Ticket</th>
              <th>Leg</th>
              <th>Player</th>
              <th>Prop</th>
              <th>Dir</th>
              <th>Line</th>
              <th>Actual</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {"".join(leg_rows)}
          </tbody>
        </table>
      </div>
    </div>
    """
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Ticket Eval — {display_date}</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<style>{CSS}</style>
</head>
<body>

<header>
  <div class="logo-title">TICKET EVALUATION</div>
  <div class="logo-sub">{display_date}</div>
  <div class="date-badge">📅 {display_date}</div>
</header>

<div class="main">
  {kpi_html}
  {tickets_html}
  {legs_html}
  <div class="footer">Generated {generated} — {graded_path.name}</div>
</div>

</body>
</html>"""
    
    return html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",    type=str)
    parser.add_argument("--graded",  type=str, required=True)
    parser.add_argument("--out",     type=str, required=True)
    args = parser.parse_args()
    
    graded_path = Path(args.graded).resolve()
    if not graded_path.exists():
        print(f"ERROR: Not found: {graded_path}")
        sys.exit(1)
    
    html = build_html(graded_path)
    if not html:
        sys.exit(1)
    
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✅ Wrote ticket eval HTML → {out}")
    print(f"   {len(html):,} bytes")


if __name__ == "__main__":
    main()
