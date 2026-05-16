import json
from pathlib import Path

import pandas as pd

data = json.loads(Path("outputs/tickets/uniform_tickets_2026-05-08_top.json").read_text(encoding="utf-8"))
print(f"Total tickets: {len(data)}\n")

target_sizes = (3, 4, 5)
shown: dict[tuple[int, str], list[dict]] = {}
for t in data:
    key = (t["size"], t["bucket"])
    if t["size"] not in target_sizes:
        continue
    shown.setdefault(key, [])
    if len(shown[key]) < 1:
        shown[key].append(t)

for key in sorted(shown):
    size, bucket = key
    for t in shown[key]:
        joint = t["joint_p_hit"]
        payout = t["power_payout"]
        ev = t["expected_profit_per_$1"]
        print(f"=== {size}-leg {bucket}  joint_p={joint:.1%}  payout={payout}x  EV/$1=${ev:+.2f}")
        for leg in t["legs"]:
            sport = str(leg.get("sport", ""))[:6]
            player = str(leg.get("player", ""))[:22]
            prop = str(leg.get("prop", ""))[:18]
            direction = str(leg.get("direction", ""))[:5]
            line = leg.get("line")
            pt = str(leg.get("pick_type", ""))[:8]
            est_p = float(leg.get("est_p", 0.0))
            res = leg.get("result")
            print(
                f"   {sport:6} {player:22}  {prop:18}  {direction:5} {line}  "
                f"pt={pt:8}  est_p={est_p:.0%}  res={res}"
            )
        print()
