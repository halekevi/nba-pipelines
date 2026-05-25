#!/usr/bin/env python3
import datetime
import json
import os
from pathlib import Path

import pandas as pd

root = Path(__file__).resolve().parent.parent

meta = root / "models" / "edge_model_metadata.json"
if meta.exists():
    m = json.loads(meta.read_text(encoding="utf-8"))
    print("=== MODEL METADATA ===")
    print(f"  trained_at:   {m.get('trained_at') or m.get('timestamp') or 'not found'}")
    print(f"  dataset_rows: {m.get('n_train') or m.get('train_rows') or 'not found'}")
    print(f"  source_csv:   {m.get('source_csv') or m.get('dataset') or 'not found'}")
    print(f"  soccer_auc:   {m.get('soccer_auc') or m.get('auc_soccer') or 'not found'}")
    print()
else:
    print("No edge_model_metadata.json found")
    print()

ds = root / "data" / "retrain_dataset.csv"
pkl = root / "models" / "edge_model_unified.pkl"
if ds.exists() and pkl.exists():
    ds_mt = os.path.getmtime(ds)
    pkl_mt = os.path.getmtime(pkl)
    print(f"  dataset mtime: {datetime.datetime.fromtimestamp(ds_mt)}")
    print(f"  model   mtime: {datetime.datetime.fromtimestamp(pkl_mt)}")
    if ds_mt > pkl_mt:
        print("  --> DATASET IS NEWER THAN MODEL (model was NOT retrained on backfill data)")
    else:
        print("  --> Model is newer than or same age as dataset (was retrained)")
    print()

df = pd.read_csv(ds, low_memory=False)
soc = df[df["sport"] == "Soccer"].copy()
unk = soc["opp_team"].astype(str).str.upper().isin(["", "NAN", "UNKNOWN_OPP", "UNKNOWN", "NONE"])
same = soc[
    soc["team"].astype(str).str.strip().str.upper()
    == soc["opp_team"].astype(str).str.strip().str.upper()
]
print(f"Soccer rows:           {len(soc):,}")
print(f"Still UNKNOWN_OPP:     {unk.sum():,} ({unk.mean():.1%})")
print(f"Self-match (team==opp): {len(same):,}")
if len(same):
    cols = [c for c in ["team", "opp_team", "player", "game_date", "file_date"] if c in same.columns]
    print(same[cols].head(5).to_string())
