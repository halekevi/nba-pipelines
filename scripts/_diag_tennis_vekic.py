import json
import pandas as pd
from pathlib import Path

# 1. Inspect a step8 Tennis xlsx — exact columns and sample rows
s8_path = Path("outputs/2026-05-09/step8_tennis_direction_clean_2026-05-09.xlsx")
if not s8_path.exists():
    s8_path = Path("outputs/2026-05-09/tennis/step8_tennis_direction_clean_2026-05-09.xlsx")

print(f"Using step8: {s8_path} (exists={s8_path.exists()})")
s8 = pd.read_excel(s8_path, engine="openpyxl", dtype=str).fillna("")
print(f"Step8 columns: {list(s8.columns)}")
print(f"Step8 rows: {len(s8)}")
print()

vekic = s8[s8.apply(lambda r: "vekic" in str(r.values).lower(), axis=1)]
print(f"Donna Vekic in step8 ({len(vekic)} rows):")
print(vekic.to_string())
print()

# 2. What does the graded JSON have for Donna Vekic on this date?
gj = Path("ui_runner/templates/graded_props_2026-05-09.json")
data = json.loads(gj.read_text(encoding="utf-8"))
entries = data if isinstance(data, list) else data.get("picks") or data.get("props") or []
vekic_graded = [
    e
    for e in entries
    if "vekic" in str(e.get("player", "")).lower()
    and str(e.get("sport", "")).upper() == "TENNIS"
]
print(f"Donna Vekic in graded JSON ({len(vekic_graded)} rows):")
for e in vekic_graded:
    print(
        f"  player={e.get('player')} prop={e.get('stat_type') or e.get('prop')} "
        f"line={e.get('line')} outcome={e.get('outcome') or e.get('result')}"
    )
