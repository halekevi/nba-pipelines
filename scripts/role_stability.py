#!/usr/bin/env python3
"""Role stability score from recent minutes / TOI samples."""

from __future__ import annotations

from typing import Iterable, List, Optional

import numpy as np
import pandas as pd


def role_stability(minutes_list: Iterable[object]) -> Optional[float]:
    arr = np.array([float(m) for m in minutes_list if m is not None and m != "" and not pd.isna(m) and float(m) > 0])
    if len(arr) < 3:
        return None
    cv = float(arr.std() / arr.mean()) if arr.mean() else 1.0
    return round(1 - min(cv, 1.0), 3)


def attach_role_stability_columns(df: pd.DataFrame, minutes_col: str = "minutes_L10_list") -> pd.DataFrame:
    out = df.copy()
    if minutes_col not in out.columns:
        out[minutes_col] = [[] for _ in range(len(out))]
    out["role_stability_score"] = out[minutes_col].apply(role_stability)
    out["high_variance_role"] = pd.to_numeric(out["role_stability_score"], errors="coerce").lt(0.35)
    return out
