"""Lift finder: for each (sport, pick_group) bucket, find single-feature gates
that meaningfully raise hit rate while keeping enough volume to be useful.

Inputs: every mobile/www/graded_props_*.json
Outputs: outputs/winners_by_pick_type/lift_recommendations.csv
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
NUMERIC_FEATURES = ["ml_prob"]


def _wilson_low(hits: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - spread) / denom)


def _line_bucket(v: float) -> str:
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
                r = dict(r)
                r["_date"] = f.name.split("graded_props_")[-1].split(".")[0]
                rows.append(r)
    return pd.DataFrame(rows)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    res = df["result"].astype(str).str.strip().str.upper()
    df = df[res.isin(["HIT", "MISS"])].copy()
    df["is_hit"] = (res.loc[df.index] == "HIT").astype(int)

    pt = df["pick_type"].astype(str).str.strip().str.lower()
    direction = df["direction"].astype(str).str.strip().str.upper()
    bad = pt.isin(["goblin", "demon"]) & direction.eq("UNDER")
    df = df.loc[~bad].copy()

    base = pd.Series("Standard", index=df.index, dtype=str)
    base.loc[pt.eq("goblin")] = "Goblin"
    base.loc[pt.eq("demon")] = "Demon"
    df["pick_base"] = base
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
    df["edge_n"] = pd.to_numeric(df["edge"], errors="coerce")

    for c in CATEGORICAL_FEATURES:
        if c in df.columns:
            s = df[c].astype(str).str.strip()
            df[c] = s.replace({"": "(missing)", "nan": "(missing)", "None": "(missing)"})

    av = df["actual_value"].astype(str).str.strip()
    df["_zero_actual"] = av.isin(["0.0", "0", "0.00", ""]).astype(int)
    grade_q = df.groupby(["sport_u", "prop_u"])["_zero_actual"].mean().reset_index()
    grade_q = grade_q.rename(columns={"_zero_actual": "pct_actual_zero_blank"})
    df = df.merge(grade_q, on=["sport_u", "prop_u"], how="left")
    df = df[df["pct_actual_zero_blank"] < 0.85].copy()
    return df


def _categorical_lift(
    df: pd.DataFrame, feat: str, base_hr: float, base_n: int, min_keep_frac: float
) -> dict | None:
    s = df[feat]
    g = (
        df.groupby(s, dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
    )
    g["hr"] = g["hits"] / g["n"]
    g = g[g["n"] >= max(50, int(min_keep_frac * base_n))]
    if g.empty:
        return None
    best_val = g["hr"].idxmax()
    best = g.loc[best_val]
    hr = float(best["hr"])
    n = int(best["n"])
    if hr - base_hr < 0.02:
        return None
    return {
        "feature": feat,
        "rule": f'{feat} == "{best_val}"',
        "n": n,
        "hits": int(best["hits"]),
        "hit_rate": hr,
        "wilson_low": _wilson_low(int(best["hits"]), n),
        "lift_vs_base": hr - base_hr,
        "kept_frac": n / max(1, base_n),
    }


def _numeric_lift(
    df: pd.DataFrame, feat: str, base_hr: float, base_n: int, min_keep_frac: float
) -> dict | None:
    s = pd.to_numeric(df[feat], errors="coerce")
    valid = df[s.notna()].copy()
    if valid.empty:
        return None
    sv = pd.to_numeric(valid[feat], errors="coerce")
    qs = np.unique(np.quantile(sv.dropna(), [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90]))
    best: dict | None = None
    for thr in qs:
        kept = valid[sv >= thr]
        n = int(len(kept))
        if n < max(50, int(min_keep_frac * base_n)):
            continue
        hr = float(kept["is_hit"].mean())
        wl = _wilson_low(int(kept["is_hit"].sum()), n)
        lift = hr - base_hr
        if lift < 0.02:
            continue
        cand = {
            "feature": feat,
            "rule": f"{feat} >= {thr:.4f}",
            "n": n,
            "hits": int(kept["is_hit"].sum()),
            "hit_rate": hr,
            "wilson_low": wl,
            "lift_vs_base": lift,
            "kept_frac": n / max(1, base_n),
        }
        if best is None or cand["wilson_low"] > best["wilson_low"]:
            best = cand
    return best


def _bucket_baseline(df: pd.DataFrame) -> dict:
    n = int(len(df))
    hits = int(df["is_hit"].sum())
    return {"n": n, "hits": hits, "hit_rate": hits / max(1, n)}


def main() -> None:
    raw = _load()
    print(f"Loaded {len(raw):,} graded rows.")
    df = _prep(raw)
    print(f"Trusted decided rows: {len(df):,} (broken-grade groups dropped)")

    rows: list[dict] = []
    for (sport, pg), bucket in df.groupby(["sport_u", "pick_group"], dropna=False):
        if len(bucket) < 500:
            continue
        base = _bucket_baseline(bucket)
        # Categorical features
        for f in CATEGORICAL_FEATURES:
            if f not in bucket.columns:
                continue
            if bucket[f].nunique(dropna=True) <= 1:
                continue
            r = _categorical_lift(bucket, f, base["hit_rate"], base["n"], 0.10)
            if r is None:
                continue
            rows.append({"sport": sport, "pick_group": pg, "base_n": base["n"], "base_hit_rate": base["hit_rate"], **r})
        for f in NUMERIC_FEATURES:
            col = f + "_n"
            if col not in bucket.columns:
                continue
            r = _numeric_lift(bucket, col, base["hit_rate"], base["n"], 0.10)
            if r is None:
                continue
            r["feature"] = f
            r["rule"] = r["rule"].replace(col, f)
            rows.append({"sport": sport, "pick_group": pg, "base_n": base["n"], "base_hit_rate": base["hit_rate"], **r})

    out = pd.DataFrame(rows)
    if out.empty:
        print("No lift candidates found.")
        return
    out = out.sort_values(["sport", "pick_group", "wilson_low"], ascending=[True, True, False])
    out_path = OUT_DIR / "lift_recommendations.csv"
    out.to_csv(out_path, index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))

    # Show top filter per (sport, pick_group)
    top = out.sort_values(["sport", "pick_group", "wilson_low"], ascending=[True, True, False]) \
            .groupby(["sport", "pick_group"], as_index=False).head(3)
    print("\n=== TOP 3 SINGLE-FEATURE GATES PER (SPORT, PICK GROUP) ===")
    cols = ["sport", "pick_group", "base_n", "base_hit_rate", "feature", "rule",
            "n", "hits", "hit_rate", "wilson_low", "lift_vs_base", "kept_frac"]
    print(top[cols].to_string(index=False))

    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
