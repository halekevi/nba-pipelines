"""Stacked-filter evaluator: for each (sport, pick_group), apply the top
categorical gate AND a ml_prob floor (Q60) AND show the resulting hit rate
and kept volume. This is the "strict mode" we'd recommend running.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
GRADED_DIR = REPO / "mobile" / "www"
OUT_DIR = REPO / "outputs" / "winners_by_pick_type"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CATEGORICAL_FEATURES = [
    "tier",
    "def_tier",
    "minutes_tier",
    "role_tier",
    "h2h_bucket",
    "game_total_bucket",
    "line_bucket",
    "over_under",
]


def _wilson_low(hits: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - spread) / denom)


def _line_bucket(v) -> str:
    try:
        x = float(v)
    except Exception:
        return "(missing)"
    if not np.isfinite(x):
        return "(missing)"
    if x < 1.0:
        return "micro"
    if x < 3.0:
        return "low"
    if x < 8.0:
        return "mid"
    if x < 20.0:
        return "high"
    return "xl"


def _load() -> pd.DataFrame:
    rows: list[dict] = []
    for f in sorted(GRADED_DIR.glob("graded_props_*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in payload.get("props", []) or []:
            if isinstance(r, dict):
                rows.append(r)
    df = pd.DataFrame(rows)
    res = df["result"].astype(str).str.upper()
    df = df[res.isin(["HIT", "MISS"])].copy()
    df["is_hit"] = (res.loc[df.index] == "HIT").astype(int)
    pt = df["pick_type"].astype(str).str.strip().str.lower()
    direction = df["direction"].astype(str).str.strip().str.upper()
    bad = pt.isin(["goblin", "demon"]) & direction.eq("UNDER")
    df = df.loc[~bad].copy()
    base = pd.Series("Standard", index=df.index, dtype=str)
    base.loc[pt.eq("goblin")] = "Goblin"
    base.loc[pt.eq("demon")] = "Demon"
    pg = base.copy()
    std = pg.eq("Standard")
    pg.loc[std & direction.eq("OVER")] = "Standard OVER"
    pg.loc[std & direction.eq("UNDER")] = "Standard UNDER"
    pg.loc[std & ~direction.isin(["OVER", "UNDER"])] = "Standard (no dir)"
    df["pick_group"] = pg
    df["sport_u"] = df["sport"].astype(str).str.upper().str.strip()
    df["prop_u"] = df["prop"].astype(str).str.strip()
    df["line_num"] = pd.to_numeric(df["line"], errors="coerce")
    df["line_bucket"] = df["line_num"].map(_line_bucket)
    df["ml_prob_n"] = pd.to_numeric(df["ml_prob"], errors="coerce")
    for c in CATEGORICAL_FEATURES:
        if c in df.columns:
            s = df[c].astype(str).str.strip()
            df[c] = s.replace({"": "(missing)", "nan": "(missing)", "None": "(missing)"})

    av = df["actual_value"].astype(str).str.strip()
    df["_zero_actual"] = av.isin(["0.0", "0", "0.00", ""]).astype(int)
    grade_q = df.groupby(["sport_u", "prop_u"])["_zero_actual"].mean().reset_index().rename(
        columns={"_zero_actual": "pct_actual_zero_blank"}
    )
    df = df.merge(grade_q, on=["sport_u", "prop_u"], how="left")
    df = df[df["pct_actual_zero_blank"] < 0.85].copy()
    return df


def _best_categorical(bucket: pd.DataFrame, base_hr: float, base_n: int) -> tuple[str | None, str | None, float, int, int]:
    best = None  # (feat, val, hr, hits, n, wilson_low)
    for f in CATEGORICAL_FEATURES:
        if f not in bucket.columns:
            continue
        vals = bucket[f].astype(str)
        if vals.nunique(dropna=True) <= 1:
            continue
        g = bucket.groupby(vals).agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
        g["hr"] = g["hits"] / g["n"]
        g = g[g["n"] >= max(50, int(0.10 * base_n))]
        if g.empty:
            continue
        for val, row in g.iterrows():
            wl = _wilson_low(int(row["hits"]), int(row["n"]))
            if best is None or wl > best[5]:
                best = (f, val, float(row["hr"]), int(row["hits"]), int(row["n"]), wl)
    if best is None:
        return None, None, 0.0, 0, 0
    return best[0], best[1], best[2], best[4], best[3]


def main() -> None:
    df = _load()
    rows: list[dict] = []
    for (sport, pg), bucket in df.groupby(["sport_u", "pick_group"], dropna=False):
        if len(bucket) < 500:
            continue
        base_n = int(len(bucket))
        base_hr = float(bucket["is_hit"].mean())

        # Best categorical filter
        feat, val, _, _, _ = _best_categorical(bucket, base_hr, base_n)

        # ml_prob Q60 floor (only where defined)
        ml = pd.to_numeric(bucket["ml_prob_n"], errors="coerce")
        ml_q60 = float(np.nanquantile(ml.dropna(), 0.60)) if ml.notna().any() else None

        # Stacked filter
        mask = pd.Series(True, index=bucket.index)
        rule_parts = []
        if feat is not None and val is not None:
            mask &= bucket[feat].astype(str).eq(str(val))
            rule_parts.append(f'{feat}=="{val}"')
        if ml_q60 is not None:
            mask &= ml.fillna(-1.0).ge(ml_q60)
            rule_parts.append(f"ml_prob>={ml_q60:.3f}")

        kept = bucket.loc[mask]
        n_kept = int(len(kept))
        hr_kept = float(kept["is_hit"].mean()) if n_kept > 0 else 0.0
        wl_kept = _wilson_low(int(kept["is_hit"].sum()), n_kept) if n_kept > 0 else 0.0

        rows.append({
            "sport": sport,
            "pick_group": pg,
            "base_n": base_n,
            "base_hit_rate": base_hr,
            "rule": " AND ".join(rule_parts) if rule_parts else "(none)",
            "stacked_n": n_kept,
            "stacked_kept_frac": n_kept / max(1, base_n),
            "stacked_hit_rate": hr_kept,
            "stacked_wilson_low": wl_kept,
            "stacked_lift": hr_kept - base_hr,
        })

    out = pd.DataFrame(rows).sort_values(["sport", "pick_group"])
    out.to_csv(OUT_DIR / "stacked_filter_recommendations.csv", index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))

    print("=== STACKED FILTER (best categorical + ml_prob>=Q60) PER (SPORT, PICK GROUP) ===")
    cols = [
        "sport", "pick_group", "base_n", "base_hit_rate", "rule",
        "stacked_n", "stacked_kept_frac", "stacked_hit_rate",
        "stacked_wilson_low", "stacked_lift",
    ]
    print(out[cols].to_string(index=False))


if __name__ == "__main__":
    main()
