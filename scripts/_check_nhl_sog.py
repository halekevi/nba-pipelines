import json
from pathlib import Path

import pandas as pd

dates = ["2026-04-15", "2026-04-25", "2026-05-01", "2026-05-05", "2026-05-07"]
for d in dates:
    p = Path(f"mobile/www/graded_props_{d}.json")
    if not p.exists():
        print(d, "MISSING")
        continue
    df = pd.DataFrame(json.loads(p.read_text(encoding="utf-8")).get("props") or [])
    nhl = df[df["sport"].astype(str).str.upper() == "NHL"]
    sog = nhl[nhl["prop"].astype(str).str.lower().isin(["shots_on_goal", "shots on goal"])]
    if len(sog) == 0:
        print(f"{d}: no NHL SOG rows")
        continue
    av = pd.to_numeric(sog["actual_value"], errors="coerce")
    res = sog["result"].astype(str).str.upper()
    decided = sog[res.isin(["HIT", "MISS"])]
    hr = (decided["result"].astype(str).str.upper() == "HIT").mean() if len(decided) else float("nan")
    counts = sog["result"].value_counts().to_dict()
    print(f"{d}  NHL SOG: rows={len(sog)} mean_actual={av.mean():.2f} hit_rate={hr:.2%}  results={counts}")
