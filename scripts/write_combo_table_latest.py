#!/usr/bin/env python3
"""Write outputs/combo_table_latest.json for /payout combo reference."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.goblin_demon_multiplier import (  # noqa: E402
    load_params,
    multiplier_summary,
    synthetic_legs_for_combo,
)

COMBO_IDS = [
    "all_standard",
    "1_goblin_90",
    "1_goblin_80",
    "1_goblin_70",
    "1_goblin_60",
    "all_goblins_80",
    "all_goblins_70",
    "all_goblins_60",
    "1_demon_110",
    "1_demon_125",
    "1_demon_140",
    "mixed_gob70_dem120",
]


def main() -> int:
    out_dir = ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "combo_table_latest.json"
    params = load_params()
    leg_counts = [2, 3, 4, 5, 6]
    combos_out = []
    for cid in COMBO_IDS:
        row: dict = {"combo_id": cid, "by_leg_count": {}}
        for n in leg_counts:
            legs = synthetic_legs_for_combo(cid, n)
            sp = multiplier_summary(legs, mode="power", params=params)
            sf = multiplier_summary(legs, mode="flex", hits=n, params=params)
            row["by_leg_count"][str(n)] = {
                "est_mult_power": sp.get("est_mult"),
                "est_mult_flex_nn": sf.get("est_mult"),
                "obs_count": 0,
            }
        combos_out.append(row)
    payload = {
        "leg_counts": leg_counts,
        "combos": combos_out,
        "params": {k: params.get(k) for k in ("G_EXP", "D_EXP", "D_SCALE", "observations_count")},
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
