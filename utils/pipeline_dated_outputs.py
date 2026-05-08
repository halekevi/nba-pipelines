"""
Copy pipeline outputs into the canonical dated folder under outputs/<YYYY-MM-DD>/.

Historically this also mirrored files into Sports/<Sport>/outputs/<YYYY-MM-DD>/,
which created duplicate dated artifacts across two trees. Canonical dated storage
is now root outputs only.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def merged_slate_datetimes(df: pd.DataFrame) -> pd.Series:
    """Prefer game_date, fall back to start_time / game_start (NHL) / other common columns."""
    idx = df.index
    gd = (
        pd.to_datetime(df["game_date"], errors="coerce")
        if "game_date" in df.columns
        else pd.Series(pd.NaT, index=idx)
    )
    st = (
        pd.to_datetime(df["start_time"], errors="coerce")
        if "start_time" in df.columns
        else pd.Series(pd.NaT, index=idx)
    )
    gs = (
        pd.to_datetime(df["game_start"], errors="coerce")
        if "game_start" in df.columns
        else pd.Series(pd.NaT, index=idx)
    )
    merged = pd.to_datetime(gd.where(gd.notna(), st), errors="coerce")
    merged = pd.to_datetime(merged.where(merged.notna(), gs), errors="coerce")
    if merged.notna().any():
        return merged
    for alt in ("Date", "date", "GAME_DATE", "slate_date"):
        if alt in df.columns:
            return pd.to_datetime(df[alt], errors="coerce")
    return pd.Series(pd.NaT, index=idx)


def earliest_slate_date_iso(df: pd.Series | pd.DataFrame) -> str | None:
    if isinstance(df, pd.Series):
        dt = pd.to_datetime(df, errors="coerce").dropna()
    else:
        dt = merged_slate_datetimes(df).dropna()
    if dt.empty:
        return None
    d = dt.min().strftime("%Y-%m-%d")
    return d if _ISO_DATE.match(d) else None


def copy_pipeline_output_to_dated_dirs(
    *,
    output_path: str | Path,
    df: pd.DataFrame,
    sport_dir_name: str,
    repo_root: Path,
) -> None:
    """After writing ``output_path``, copy the file into {repo_root}/outputs/{date}/{basename}."""
    src = Path(output_path).resolve()
    if not src.is_file():
        return
    slate_date = earliest_slate_date_iso(df)
    if not slate_date:
        return
    name = src.name
    base = repo_root / "outputs" / slate_date
    try:
        base.mkdir(parents=True, exist_ok=True)
        dest = base / name
        shutil.copy2(src, dest)
        print(f"[pipeline] Dated copy -> {dest}")
    except OSError as e:
        print(f"[pipeline] WARN: dated copy failed ({base}): {e}")
