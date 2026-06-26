"""
Shared standard_line sibling lookup + Goblin offset backfill for step2 pipelines.

These columns feed ticket payout math downstream:
- ``standard_line`` → ``delta_pct`` (line / standard) and ``line_discount_vs_standard``
  in ``scripts/combined_slate_tickets.py`` and ``utils/goblin_demon_multiplier.py``
- ``deviation_level`` → discrete Goblin/Demon multipliers in per-sport step9 builders
  (NBA, MLB, WNBA, Soccer)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Empirical medians (June 2026 sibling pairs): soccer ~1.0, tennis/mlb/wnba ~2.0
GOBLIN_STANDARD_LINE_OFFSET_BY_SPORT: dict[str, dict[int, float]] = {
    "soccer": {1: 1.0, 2: 1.5, 3: 2.0},
    "tennis": {1: 1.5, 2: 2.0, 3: 3.0},
    "default": {1: 1.5, 2: 2.0, 3: 3.0},
}

DEMON_STANDARD_LINE_OFFSET_BY_SPORT: dict[str, dict[int, float]] = {
    "soccer": {1: -1.0, 2: -2.0, 3: -3.0},
    "tennis": {1: -1.5, 2: -2.0, 3: -3.0},
    "default": {1: -1.5, 2: -2.0, 3: -3.0},
}

SPORTS_NEUTRAL_GOBLIN_NO_DISTANCE = frozenset(
    {"soccer", "tennis", "nhl", "nfl", "cfb", "cbb", "golf", "pga"}
)


def _sport_offsets_key(sport: str) -> str:
    s = str(sport or "").strip().lower()
    if s in ("pga", "livgolf", "eurogolf", "lpga"):
        return "default"
    if s in GOBLIN_STANDARD_LINE_OFFSET_BY_SPORT:
        return s
    return "default"


def _coerce_dev_level(deviation_level: object) -> int:
    try:
        dev = int(float(deviation_level))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        dev = 0
    return dev if dev > 0 else 1


def get_goblin_offsets(sport: str) -> dict[int, float]:
    return GOBLIN_STANDARD_LINE_OFFSET_BY_SPORT.get(
        _sport_offsets_key(sport), GOBLIN_STANDARD_LINE_OFFSET_BY_SPORT["default"]
    )


def get_demon_offsets(sport: str) -> dict[int, float]:
    return DEMON_STANDARD_LINE_OFFSET_BY_SPORT.get(
        _sport_offsets_key(sport), DEMON_STANDARD_LINE_OFFSET_BY_SPORT["default"]
    )


def estimate_goblin_standard_line(
    line: object,
    deviation_level: object,
    sport: str = "default",
) -> float | None:
    try:
        line_val = float(line)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(line_val):
        return None
    dev = _coerce_dev_level(deviation_level)
    offsets = get_goblin_offsets(sport)
    offset = offsets.get(dev, offsets[1])
    return float(line_val + offset)


def estimate_demon_standard_line(
    line: object,
    deviation_level: object,
    sport: str = "default",
) -> float | None:
    try:
        line_val = float(line)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(line_val):
        return None
    dev = _coerce_dev_level(deviation_level)
    offsets = get_demon_offsets(sport)
    offset = offsets.get(dev, offsets[1])
    return float(line_val + offset)


def estimate_standard_line_from_pick_type(
    pick_type: str,
    line: object,
    deviation_level: object,
    sport: str = "default",
) -> float | None:
    pt = str(pick_type or "").strip().lower()
    if "gob" in pt:
        return estimate_goblin_standard_line(line, deviation_level, sport=sport)
    if "dem" in pt:
        return estimate_demon_standard_line(line, deviation_level, sport=sport)
    try:
        return float(line)
    except (TypeError, ValueError):
        return None


def _norm_pick_type_value(s: object) -> str:
    t = str(s or "").strip().lower()
    if "gob" in t:
        return "Goblin"
    if "dem" in t:
        return "Demon"
    return "Standard"


def _assign_deviation_level(
    df: pd.DataFrame,
    line_num_col: str,
    pick_type_col: str,
    player_col: str,
    prop_norm_col: str,
) -> pd.Series:
    deviation = pd.Series(0, index=df.index, dtype=int)
    gob_dem = df[df[pick_type_col].isin(["Goblin", "Demon"])]
    if gob_dem.empty:
        return deviation

    def _rank_group(grp: pd.DataFrame) -> pd.Series:
        pt = grp.name[2] if isinstance(grp.name, tuple) else grp[pick_type_col].iloc[0]
        asc = pt != "Goblin"
        lines = grp[line_num_col].dropna().sort_values(ascending=asc)
        rmap = {v: i + 1 for i, v in enumerate(lines.unique())}
        return grp[line_num_col].map(rmap).fillna(0).astype(int)

    dev = gob_dem.groupby([player_col, prop_norm_col, pick_type_col], group_keys=False).apply(_rank_group)
    deviation.loc[dev.index] = dev.values
    return deviation


def attach_standard_line_and_deviation(
    df: pd.DataFrame,
    *,
    sport: str = "default",
    player_col: str = "player",
    prop_norm_col: str = "prop_norm",
    line_col: str = "line",
    pick_type_col: str = "pick_type",
    backfill_goblin: bool = True,
    preserve_existing_standard_line: bool = True,
    normalize_pick_type: bool = True,
) -> pd.DataFrame:
    """
    Sibling Standard lookup, deviation_level rank, and Goblin offset backfill.

    Returns a copy with ``standard_line``, ``standard_line_source``, and
    ``deviation_level`` columns added or updated.
    """
    out = df.copy()
    if player_col not in out.columns:
        for alt in ("player", "player_name", "player_id"):
            if alt in out.columns:
                player_col = alt
                break

    if pick_type_col not in out.columns:
        out[pick_type_col] = "Standard"
    elif normalize_pick_type:
        out[pick_type_col] = out[pick_type_col].map(_norm_pick_type_value)

    if line_col not in out.columns:
        for alt in ("line", "line_score"):
            if alt in out.columns:
                line_col = alt
                break

    if prop_norm_col not in out.columns:
        for alt in ("prop_norm", "stat_norm", "prop_type"):
            if alt in out.columns:
                prop_norm_col = alt
                break

    line_num = pd.to_numeric(out[line_col], errors="coerce")
    line_num.name = "_line_num"
    out["_line_num"] = line_num

    std_df = out[(out[pick_type_col] == "Standard") & line_num.notna()]
    std_lookup = std_df.groupby([player_col, prop_norm_col], dropna=False)["_line_num"].first().to_dict()

    sibling_std = out.apply(
        lambda r: std_lookup.get((r[player_col], r[prop_norm_col])), axis=1
    )
    sibling_std = pd.to_numeric(sibling_std, errors="coerce")

    if "standard_line" in out.columns and preserve_existing_standard_line:
        existing = pd.to_numeric(out["standard_line"], errors="coerce")
        out["standard_line"] = existing.where(existing.notna(), sibling_std)
        if "standard_line_source" not in out.columns:
            out["standard_line_source"] = ""
        had_existing = existing.notna()
        from_sibling = sibling_std.notna() & existing.isna()
        out.loc[had_existing & out["standard_line_source"].astype(str).str.strip().eq(""), "standard_line_source"] = (
            "pp_or_step1"
        )
        out.loc[from_sibling, "standard_line_source"] = "sibling_or_pp"
    else:
        out["standard_line"] = sibling_std
        out["standard_line_source"] = np.where(sibling_std.notna(), "sibling_or_pp", "")

    out["deviation_level"] = _assign_deviation_level(
        out, "_line_num", pick_type_col, player_col, prop_norm_col
    )

    if backfill_goblin:
        gob_missing = (
            out[pick_type_col].eq("Goblin")
            & pd.to_numeric(out["standard_line"], errors="coerce").isna()
            & out["_line_num"].notna()
        )
        if gob_missing.any():
            est = out.loc[gob_missing].apply(
                lambda r: estimate_goblin_standard_line(
                    r["_line_num"], r["deviation_level"], sport=sport
                ),
                axis=1,
            )
            est_num = pd.to_numeric(est, errors="coerce")
            filled = gob_missing & est_num.reindex(out.index).notna()
            out.loc[filled, "standard_line"] = est_num.reindex(out.index).loc[filled]
            out.loc[filled, "standard_line_source"] = "offset_estimate"

    out.drop(columns=["_line_num"], inplace=True, errors="ignore")
    return out


def log_goblin_standard_line_fill(df: pd.DataFrame, tag: str = "") -> None:
    if "pick_type" not in df.columns or "standard_line" not in df.columns:
        return
    n_gob = int((df["pick_type"] == "Goblin").sum())
    if not n_gob:
        return
    n_gob_std = int(pd.to_numeric(df.loc[df["pick_type"] == "Goblin", "standard_line"], errors="coerce").notna().sum())
    by_src: dict = {}
    if "standard_line_source" in df.columns:
        by_src = (
            df.loc[df["pick_type"] == "Goblin", "standard_line_source"]
            .value_counts()
            .to_dict()
        )
    prefix = f"{tag} " if tag else ""
    print(
        f"{prefix}Goblin standard_line fill: {n_gob_std}/{n_gob} "
        f"({100.0 * n_gob_std / n_gob:.0f}%) sources={by_src}"
    )
