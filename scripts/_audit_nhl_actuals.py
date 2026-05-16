import os
import pandas as pd

for d in ["2026-04-15", "2026-04-01", "2026-03-15", "2026-02-25"]:
    p = f"outputs/{d}/actuals_nhl_{d}.csv"
    if not os.path.exists(p):
        print(p, "MISSING")
        continue
    nhl = pd.read_csv(p)
    print(f"--- {d}: rows={len(nhl)} cols={list(nhl.columns)[:10]}")
    if "prop_type" not in nhl.columns:
        continue
    for label, alts in [
        ("SOG", ["shots on goal", "shots_on_goal", "sog"]),
        ("PPP", ["power play points", "power_play_points", "ppp"]),
        ("HITS", ["hits"]),
    ]:
        sub = nhl[nhl["prop_type"].astype(str).str.lower().isin(alts)]
        n = len(sub)
        m = sub["actual"].mean() if "actual" in sub.columns and n else float("nan")
        nz = (sub["actual"] != 0).sum() if "actual" in sub.columns and n else 0
        print(f"  {label}: rows={n} mean={m:.2f} nonzero={nz}")
