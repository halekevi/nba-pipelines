import pandas as pd
from pathlib import Path

df = pd.read_csv("data/retrain_dataset.csv", low_memory=False)
ten = df[df["sport"].str.upper() == "TENNIS"].copy()
print(f"Tennis total rows: {len(ten):,}")
print()

for col in ["rank_score", "blended_score", "def_tier", "edge_score"]:
    if col in ten.columns:
        filled = ten[col].notna()
        print(f"{col} fill: {filled.mean():.1%} ({filled.sum():,}/{len(ten):,})")
print()

if "file_date" in ten.columns and "rank_score" in ten.columns:
    by_date = ten.groupby("file_date")["rank_score"].apply(lambda s: s.notna().mean()).sort_index()
    print("Join rate by date:")
    print(by_date.to_string())
    print()

import re

outputs = Path("outputs")
date_dirs = sorted(
    [d for d in outputs.iterdir() if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)]
)[-15:]
print("Recent Tennis step8 files (date folders):")
for d in date_dirs:
    s8 = list(d.rglob("step8_tennis*.xlsx"))
    graded = Path("ui_runner/templates") / f"graded_props_{d.name}.json"
    s8_name = str(s8[0].relative_to(d)) if s8 else "MISSING"
    graded_flag = "YES" if graded.exists() else "NO"
    print(f"  {d.name}: step8={s8_name} | graded={graded_flag}")
print()
print("prop_norm in columns:", "prop_norm" in ten.columns)
if "prop_norm" in ten.columns:
    print("Joined prop_norm sample:", ten.loc[ten["rank_score"].notna(), "prop_norm"].value_counts().head(5).to_string())
    print("Unjoined prop_norm sample:", ten.loc[ten["rank_score"].isna(), "prop_norm"].value_counts().head(5).to_string())
print()

if "rank_score" in ten.columns:
    joined = ten[ten["rank_score"].notna()].head(2)
    unjoined = ten[ten["rank_score"].isna()].head(2)
    key_cols = [c for c in ["player", "prop_norm", "line", "game_date", "file_date"] if c in ten.columns]
    print("Joined rows:")
    print(joined[key_cols].to_string())
    print()
    print("Unjoined rows:")
    print(unjoined[key_cols].to_string())
