"""Same-day ET slate filter for PrizePicks step1 fetchers."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def no_props_log_line(sport_label: str, fetch_date: str) -> str:
    return (
        f"[{sport_label} step1] No props for {fetch_date} — "
        "board may be tomorrow's slate or off-season"
    )


def should_preserve_append_output(out_path: str | Path, append: bool) -> bool:
    """True when --append should keep an existing non-empty step1 CSV unchanged."""
    if not append:
        return False
    path = Path(out_path)
    if not path.is_file():
        return False
    try:
        existing = pd.read_csv(path, encoding="utf-8-sig")
        return len(existing) > 0
    except Exception:
        return False


def apply_game_date_filter(
    df: pd.DataFrame,
    target_date: str,
    tz_name: str,
    allow_nearest_future: bool,
    *,
    start_time_col: str = "start_time",
) -> tuple[pd.DataFrame, str | None]:
    """
  Filter props to fetch_date (ET calendar) unless allow_nearest_future is set.

  - allow_nearest_future False: keep only rows where date(start_time) == target_date.
  - allow_nearest_future True: skip date filter (full API board; game_date column set).
  - allow_nearest_future True with legacy nearest-future: not used when skip-all is intended;
    callers pass False for strict pipeline runs.
    """
    target_date = str(target_date or "").strip()[:10]
    if df is None or len(df) == 0:
        out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        if isinstance(out, pd.DataFrame) and "game_date" not in out.columns:
            out["game_date"] = ""
        return out, None

    tz = ZoneInfo(str(tz_name or "America/New_York"))
    col = start_time_col if start_time_col in df.columns else "start_time"
    if col not in df.columns:
        out = df.copy()
        out["game_date"] = ""
        if allow_nearest_future:
            return out, None
        return out.head(0).copy(), None

    ts = pd.to_datetime(df[col], errors="coerce", utc=True)
    out = df.copy()
    out["game_date"] = ts.dt.tz_convert(tz).dt.date.astype("string").fillna("")

    if allow_nearest_future:
        return out, None

    same_day = out.loc[out["game_date"].eq(target_date)].copy()
    return same_day, None
