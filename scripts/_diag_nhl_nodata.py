#!/usr/bin/env python3
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "data/cache/proporacle_ref.db")
df = pd.read_csv(ROOT / "outputs/2026-05-23/step4_nhl_with_stats.csv", low_memory=False)
nd = df[df["stat_status"] == "NO_DATA"]

print("=== NHL DB range ===")
r = con.execute("SELECT MIN(game_date), MAX(game_date), COUNT(*) FROM nhl").fetchone()
print(f"  {r[2]} rows, {r[0]} -> {r[1]}")
print(f"  Slate game_date sample: {df['game_date'].dropna().unique()[:3] if 'game_date' in df.columns else 'n/a'}")

print("\n=== ZERO DB rows (name not in nhl table) ===")
for name in nd["player_name"].unique():
    ct = con.execute("SELECT COUNT(*) FROM nhl WHERE lower(player)=lower(?)", (name,)).fetchone()[0]
    if ct == 0:
        pat = name.split()[-1][:6]
        alts = con.execute(
            "SELECT DISTINCT player FROM nhl WHERE player LIKE ? LIMIT 5",
            (f"%{pat}%",),
        ).fetchall()
        print(f"  {name!r} -> 0 rows; fuzzy last name: {[a[0] for a in alts]}")

print("\n=== HAS rows but NO_DATA: pp_points sample ===")
for name in ["Andrei Svechnikov", "Nick Suzuki", "Juraj Slafkovský"]:
    rows = con.execute(
        """SELECT game_date, pp_points, goals, assists, position
           FROM nhl WHERE lower(player)=lower(?) ORDER BY game_date DESC LIMIT 8""",
        (name,),
    ).fetchall()
    non_null_pp = sum(1 for x in rows if x[1] is not None)
    print(f"  {name}: {len(rows)} recent rows, pp_points non-null: {non_null_pp}")
    if rows:
        print(f"    latest: {rows[0]}")

print("\n=== Goalie NO_DATA: saves via shots_on_goal ===")
for name in ["Jakub Dobes", "Frederik Andersen"]:
    rows = con.execute(
        """SELECT game_date, shots_on_goal, goals, position
           FROM nhl WHERE lower(player)=lower(?) ORDER BY game_date DESC LIMIT 5""",
        (name,),
    ).fetchall()
    print(f"  {name}: rows={len(rows)}")
    if not rows:
        pat = name.split()[-1]
        alts = con.execute(
            "SELECT player, COUNT(*) FROM nhl WHERE player LIKE ? GROUP BY player",
            (f"%{pat}%",),
        ).fetchall()
        print(f"    DB alts: {alts}")

print("\n=== pp_points NULL rate league-wide ===")
r = con.execute(
    "SELECT COUNT(*), SUM(CASE WHEN pp_points IS NOT NULL THEN 1 ELSE 0 END) FROM nhl"
).fetchone()
print(f"  total={r[0]}, pp_points non-null={r[1]} ({100*r[1]/r[0]:.1f}%)")

g_ct = con.execute("SELECT COUNT(*) FROM nhl WHERE position='G'").fetchone()[0]
print(f"\n=== Goalie rows in DB: {g_ct} ===")
if g_ct:
    for r in con.execute(
        "SELECT player, shots_on_goal, goals FROM nhl WHERE position='G' LIMIT 5"
    ):
        print(" ", r)

con.close()
