"""
Play-side edge for prop ML training and diagnostics.

Raw slate edge is typically (projection - line), which is negative for good UNDER plays.
Step7 inference negates edge when bet_direction is UNDER so the `edge` feature is
signed toward the pick — same as edge_adj_dr / Goblin audit logic.

direction_num: 1 = OVER, 0 = UNDER (as from _direction_num in train_prop_model_*).
"""
from __future__ import annotations

import pandas as pd


def play_side_edge(edge: pd.Series, direction_num: pd.Series) -> pd.Series:
    e = pd.to_numeric(edge, errors="coerce")
    d = pd.to_numeric(direction_num, errors="coerce").fillna(1).astype(int)
    und = d.eq(0)
    return e.where(~und, -e)
