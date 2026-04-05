"""Parquet I/O for bulk feature / slate columns (DB holds pointers + keys)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_props_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def read_props_parquet(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)
