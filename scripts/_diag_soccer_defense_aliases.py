#!/usr/bin/env python3
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Sports" / "Soccer" / "scripts"))
from enrich_soccer_step8_defense import (  # noqa: E402
    _ALIASES,
    _build_defense_lookup,
    _load_defense,
    _resolve_opp_key,
    normalize_opp,
)

def_df = _load_defense()
lookup, db_keys = _build_defense_lookup(def_df)
log = Path(__file__).resolve().parents[1] / "logs" / "soccer_unmatched_teams.txt"
unmatched = []
for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
    if "unmatched:" in line:
        t = line.split("unmatched:")[1].strip().strip("'")
        unmatched.append(t)

print("Lookup key column: pp_name (normalized)")
print(f"DB unique keys: {len(db_keys)}\n")
print("Saudi / NWSL / Libertadores keys in DB:")
for k in sorted(db_keys):
    if any(x in k for x in ("hilal", "ettifaq", "pride", "reign", "boca", "penarol", "neom", "damac")):
        print(f"  {k}")

import sqlite3
import pandas as pd

con = sqlite3.connect(Path(__file__).resolve().parents[1] / "data/cache/proporacle_ref.db")
sq = pd.read_sql(
    "SELECT team, pp_name, league FROM defense WHERE sport='soccer'",
    con,
)
con.close()
print(f"\nSQLite defense soccer rows: {len(sq)}")
hilal = def_df[def_df["pp_name"].astype(str).str.upper() == "HILAL"]
print("HILAL rows in merged def_df:", len(hilal))
if len(hilal):
    print(hilal[["pp_name", "team_name" if "team_name" in hilal.columns else "pp_name"]].head())
print("hilal in lookup:", "hilal" in lookup)
print("Sample pp_name (Saudi):", sq[sq["pp_name"].astype(str).str.contains("HIL|ETT|FAT|DAM", na=False)][["pp_name", "team"]].head(10).to_string())
print("\nUnmatched log -> normalize -> resolve (current code):")
for t in unmatched:
    n = normalize_opp(t)
    key, rec = _resolve_opp_key(t, lookup, db_keys)
    status = "OK" if rec else "MISS"
    alias = _ALIASES.get(n, "")
    print(f"  {status:4} {t:24} norm={n:24} alias={alias or '-'}")
