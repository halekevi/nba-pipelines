#!/usr/bin/env python3
import sqlite3
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATE = "2026-05-14"
with sqlite3.connect(REPO / "data" / "line_history.db") as c:
    n = c.execute(
        "SELECT COUNT(*) FROM line_history WHERE sport='NBA' AND fetched_at LIKE ?",
        (f"{DATE}%",),
    ).fetchone()[0]
print(f"line_history NBA {DATE}: {n}")
s1 = REPO / "outputs" / DATE / "nba" / "step1_pp_props_today.csv"
print(f"step1 rows: {len(pd.read_csv(s1))}")
s4 = REPO / "outputs" / DATE / "nba" / "step4_with_stats.csv"
if s4.is_file():
    print(f"step4 rows: {len(pd.read_csv(s4))}")
