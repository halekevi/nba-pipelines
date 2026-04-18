#!/usr/bin/env python3
"""
render_combined_slate_latest.py
--------------------------------
Finds newest combined_slate_tickets_YYYY-MM-DD.xlsx and renders it to:
  docs/combined_slate_latest.html
and also:
  docs/combined_slate_<date>.html

Dark theme + search + sortable columns (client-side).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import datetime
import pandas as pd

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"

PATTERN = re.compile(r"^combined_slate_tickets_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)

# If you keep combined slate files in a specific folder, add it here:
SEARCH_DIRS = [
    ROOT,
    ROOT / "outputs",
]


def find_latest_combined_slate() -> tuple[Path, str]:
    candidates: list[tuple[str, Path]] = []

    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        for p in d.glob("combined_slate_tickets_*.xlsx"):
            m = PATTERN.match(p.name)
            if m:
                date_str = m.group(1)
                candidates.append((date_str, p))

    if candidates:
        # pick max by date in filename
        candidates.sort(key=lambda x: x[0])
        date_str, path = candidates[-1]
        return path, date_str

    # fallback: use most recently modified file matching prefix anywhere in ROOT
    all_files = list(ROOT.rglob("combined_slate_tickets_*.xlsx"))
    if not all_files:
        raise FileNotFoundError("No combined_slate_tickets_*.xlsx found in repo.")
    latest = max(all_files, key=lambda p: p.stat().st_mtime)

    m = PATTERN.match(latest.name)
    date_str = m.group(1) if m else datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d")
    return latest, date_str


def read_all_sheets(xlsx_path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(xlsx_path)
    frames = []
    for sheet in xl.sheet_names:
        df = xl.parse(sheet, dtype=str).fillna("")
        if df.empty:
            continue
        df.insert(0, "_sheet", sheet)
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)

    # Light cleanup: avoid huge columns, keep order
    # (You can customize this list later)
    preferred_first = [
        "_sheet",
        "tier",
        "pick_type",
        "bet_dir",
        "player",
        "team",
        "opp_team",
        "prop_type",
        "line",
        "edge",
        "abs_edge",
        "rank_score",
    ]
    cols = list(out.columns)
    ordered = [c for c in preferred_first if c in cols] + [c for c in cols if c not in preferred_first]
    out = out[ordered]

    return out


def df_to_html(df: pd.DataFrame, title: str, subtitle: str) -> str:
    # escape=False is risky if you have HTML in cells; we keep escape=True
    table_html = df.to_html(index=False, escape=True, classes="data-table", border=0)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
  :root {{
    --bg: #0b0f14;
    --panel: #0f1620;
    --text: #e8eef6;
    --muted: #9fb0c3;
    --line: rgba(255,255,255,0.08);
    --accent: #6aa9ff;
    --good: #35d07f;
    --bad: #ff5c5c;
    --warn: #ffd166;
  }}

  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
  }}

  .wrap {{
    max-width: none;
    width: 100%;
    margin: 0 auto;
    padding: 18px 14px 40px;
    box-sizing: border-box;
  }}

  .header {{
    display: flex;
    gap: 12px;
    align-items: flex-end;
    justify-content: space-between;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }}

  h1 {{
    margin: 0;
    font-size: 22px;
    letter-spacing: 0.2px;
  }}

  .sub {{
    margin-top: 6px;
    color: var(--muted);
    font-size: 13px;
  }}

  .controls {{
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }}

  .search {{
    background: var(--panel);
    border: 1px solid var(--line);
    color: var(--text);
    padding: 10px 12px;
    border-radius: 10px;
    width: 320px;
    outline: none;
  }}

  .pill {{
    background: var(--panel);
    border: 1px solid var(--line);
    color: var(--muted);
    padding: 8px 10px;
    border-radius: 999px;
    font-size: 12px;
  }}

  .table-wrap {{
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: hidden;
  }}

  table.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }}

  thead th {{
    position: sticky;
    top: 0;
    background: #0f1926;
    z-index: 2;
    text-align: left;
    padding: 10px 10px;
    border-bottom: 1px solid var(--line);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }}

  tbody td {{
    padding: 9px 10px;
    border-bottom: 1px solid var(--line);
    vertical-align: top;
  }}

  tbody tr:hover {{
    background: rgba(255,255,255,0.03);
  }}

  .muted {{
    color: var(--muted);
  }}

  .note {{
    margin-top: 10px;
    color: var(--muted);
    font-size: 12px;
  }}

  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.03);
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div>
        <h1>{title}</h1>
        <div class="sub">{subtitle}</div>
      </div>

      <div class="controls">
        <input id="search" class="search" placeholder="Search player / team / prop / anything…" />
        <span id="rowcount" class="pill"></span>
        <span class="pill">Click headers to sort</span>
      </div>
    </div>

    <div class="table-wrap">
      {table_html}
    </div>

    <div class="note">
      <span class="badge">Tip</span>
      Use search + sort to quickly build slips.
    </div>
  </div>

<script>
(function() {{
  const table = document.querySelector("table.data-table");
  const tbody = table.querySelector("tbody");
  const search = document.getElementById("search");
  const rowcount = document.getElementById("rowcount");
  let rows = Array.from(tbody.querySelectorAll("tr"));

  function updateCount() {{
    const visible = rows.filter(r => r.style.display !== "none").length;
    rowcount.textContent = visible + " rows";
  }}

  // Search filter
  search.addEventListener("input", () => {{
    const q = search.value.trim().toLowerCase();
    rows.forEach(r => {{
      const txt = r.innerText.toLowerCase();
      r.style.display = (q === "" || txt.includes(q)) ? "" : "none";
    }});
    updateCount();
  }});

  // Sort by column
  const ths = table.querySelectorAll("thead th");
  let sortCol = -1;
  let sortAsc = true;

  function parseCell(val) {{
    const v = val.trim();
    // numeric detect
    const n = Number(v.replace(/[%,$]/g, ""));
    if (!Number.isNaN(n) && v !== "") return n;
    return v.toLowerCase();
  }}

  ths.forEach((th, idx) => {{
    th.addEventListener("click", () => {{
      sortAsc = (sortCol === idx) ? !sortAsc : true;
      sortCol = idx;

      rows.sort((a, b) => {{
        const av = parseCell(a.children[idx]?.innerText ?? "");
        const bv = parseCell(b.children[idx]?.innerText ?? "");
        if (av < bv) return sortAsc ? -1 : 1;
        if (av > bv) return sortAsc ? 1 : -1;
        return 0;
      }});

      rows.forEach(r => tbody.appendChild(r));
      updateCount();
    }});
  }});

  updateCount();
}})();
</script>
</body>
</html>
"""


def main():
    DOCS.mkdir(parents=True, exist_ok=True)

    xlsx_path, date_str = find_latest_combined_slate()
    df = read_all_sheets(xlsx_path)

    title = "Combined Slate (Latest)"
    subtitle = f"Source: {xlsx_path.name} • Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    html = df_to_html(df, title=title, subtitle=subtitle)

    out_latest = DOCS / "combined_slate_latest.html"
    out_dated = DOCS / f"combined_slate_{date_str}.html"

    out_latest.write_text(html, encoding="utf-8")
    out_dated.write_text(html, encoding="utf-8")

    print(f"✅ Rendered: {out_latest}")
    print(f"✅ Rendered: {out_dated}")


if __name__ == "__main__":
    main()