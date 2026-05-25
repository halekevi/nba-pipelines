#!/usr/bin/env python3
import json
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "ui_runner/templates/tickets_winrate_latest.json"
d = json.loads(p.read_text(encoding="utf-8"))
groups = d.get("groups", d) if isinstance(d, dict) else d
for g in groups[:3]:
    t = g["tickets"][0] if isinstance(g, dict) and g.get("tickets") else g
    legs = t.get("legs", [])
    p_win = t.get("p_win", 0)
    print(f"p_win={p_win}")
    for leg in legs:
        player = str(leg.get("player", ""))[:20]
        print(
            f"  player={player} prob={leg.get('leg_prob_used')} "
            f"source={leg.get('leg_prob_source')} hr={leg.get('composite_hit_rate')}"
        )
    print()
