"""One-off verification for leg_prob + payout JSON (run from repo root)."""
import json
from pathlib import Path

import pandas as pd

from scripts.combined_slate_tickets import _resolve_leg_prob, build_ticket_payout_json

leg_syn = {
    "direction": "OVER",
    "hit_rate": 1.0,
    "l5_over": 5.0,
    "ml_prob": 0.72,
    "over_hit_rate": None,
    "under_hit_rate": None,
}
p, src = _resolve_leg_prob(pd.Series(leg_syn))
print("Synthetic leg_prob_used:", p, "source:", src)

p = Path("ui_runner/templates/tickets_latest.json")
if not p.exists():
    print("No tickets_latest.json")
    raise SystemExit(0)

data = json.loads(p.read_text(encoding="utf-8"))
found = False
for g in data.get("groups") or []:
    gn = str(g.get("group_name") or "")
    if "NBA" in gn and "Flex" in gn and "3" in gn:
        found = True
        for t in g.get("tickets") or []:
            print("---", gn, "ticket_no", t.get("ticket_no"))
            legs = t.get("legs") or []
            for i, L in enumerate(legs[:6]):
                lp, ls = _resolve_leg_prob(pd.Series(L))
                hr = L.get("hit_rate")
                lo = L.get("l5_over")
                ml = L.get("ml_prob")
                print(
                    f"  leg{i} leg_prob_used={lp:.4f} source={ls} "
                    f"hit_rate={hr} l5_o={lo} ml={ml}"
                )
            print("  est_win_prob (JSON, stale until rebuild):", t.get("est_win_prob"))
            pay = t.get("payout") or {}
            print("  payout.payout (min guar):", pay.get("payout"))
            print("  sweep_payout:", pay.get("sweep_payout"))
            print("  payout_confidence_score:", pay.get("payout_confidence_score"))
            rows = [{**x, "sport": x.get("sport") or "NBA"} for x in legs]
            rebuilt = build_ticket_payout_json(gn, rows)
            if rebuilt:
                print("  rebuilt sweep_payout:", rebuilt.get("sweep_payout"))
                print("  rebuilt payout_confidence_score:", rebuilt.get("payout_confidence_score"))
        break
if not found:
    print("No NBA Flex 3-Leg group in JSON.")
