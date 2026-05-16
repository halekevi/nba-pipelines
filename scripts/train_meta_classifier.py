"""Per-bucket meta-classifier with walk-forward backtest.

For each (sport, pick_group) bucket above a minimum sample threshold, train
a small classifier (Logistic Regression with isotonic calibration) on rows
from all dates strictly before T and predict P(hit) for date T. Repeats per
date so the backtest is honest — no leakage from future slates.

Features (all known pre-game):
  - ml_prob (numeric)
  - line (numeric)
  - tier ordinal (A=3, B=2, C=1, D=0)
  - def_tier ordinal (Elite=4 .. Weak=0)
  - minutes_tier ordinal (HIGH=2, MEDIUM=1, LOW=0)
  - line_bucket ordinal
  - role_tier (one-hot)
  - h2h_bucket (one-hot)
  - game_total_bucket (one-hot)
  - over_under (binary)
  - prop top-N one-hot (within bucket)

Outputs
  outputs/meta_classifier/<sport>_<pick_group>_backtest.csv
      Per-row predictions for every backtest date.
  outputs/meta_classifier/summary.csv
      Per (sport, pick_group, decile) hit rate vs baseline.

Usage
    python scripts/train_meta_classifier.py
    python scripts/train_meta_classifier.py --buckets "NBA:Goblin" "MLB:Demon"
    python scripts/train_meta_classifier.py --since 2026-04-01 --min-train-n 800
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import OneHotEncoder

REPO = Path(__file__).resolve().parent.parent
GRADED_DIR = REPO / "mobile" / "www"
OUT_DIR = REPO / "outputs" / "meta_classifier"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MIN_TRAIN_N = 600
MIN_PREDICT_N = 25


# ── feature engineering ────────────────────────────────────────────────────

_TIER_ORD = {"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.0}
_DEF_TIER_ORD = {
    "elite": 4.0,
    "above avg": 3.0,
    "above_avg": 3.0,
    "good": 3.0,
    "solid": 3.0,
    "avg": 2.0,
    "average": 2.0,
    "neutral": 2.0,
    "mid": 2.0,
    "below avg": 1.0,
    "below_avg": 1.0,
    "weak": 0.0,
    "poor": 0.0,
}
_MIN_TIER_ORD = {"high": 2.0, "medium": 1.0, "med": 1.0, "low": 0.0}
_LINE_BUCKET_ORD = {"micro": 0.0, "low": 1.0, "mid": 2.0, "high": 3.0, "xl": 4.0}


def _line_bucket(v) -> str:
    try:
        x = float(v)
    except Exception:
        return "(missing)"
    if x != x:
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


def _pick_group(pick_type: str, direction: str) -> str:
    pt = str(pick_type or "").strip().lower()
    d = str(direction or "").strip().upper()
    if pt == "goblin":
        return "Goblin"
    if pt == "demon":
        return "Demon"
    if d == "OVER":
        return "Standard OVER"
    if d == "UNDER":
        return "Standard UNDER"
    return "Standard (no dir)"


def _wilson_low(hits: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - spread) / denom)


def _load_all_graded() -> pd.DataFrame:
    rows: list[dict] = []
    for f in sorted(GRADED_DIR.glob("graded_props_*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = f.stem.replace("graded_props_", "")
        for r in payload.get("props", []) or []:
            if isinstance(r, dict):
                r2 = dict(r)
                r2["_date"] = date
                rows.append(r2)
    return pd.DataFrame(rows)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    res = df["result"].astype(str).str.upper()
    df = df[res.isin(["HIT", "MISS"])].copy()
    df["is_hit"] = (res.loc[df.index] == "HIT").astype(int)

    pt_lower = df["pick_type"].astype(str).str.strip().str.lower()
    direction = df["direction"].astype(str).str.strip().str.upper()
    bad = pt_lower.isin(["goblin", "demon"]) & direction.eq("UNDER")
    df = df.loc[~bad].copy()

    df["sport_u"] = df["sport"].astype(str).str.upper().str.strip()
    df["pick_group"] = [_pick_group(p, d) for p, d in zip(df["pick_type"], df["direction"])]
    df["prop_u"] = df["prop"].astype(str).str.strip()
    df["line_num"] = pd.to_numeric(df["line"], errors="coerce")
    df["line_bucket"] = df["line_num"].map(_line_bucket)
    df["ml_prob_n"] = pd.to_numeric(df["ml_prob"], errors="coerce")

    df["tier_ord"] = (
        df["tier"].astype(str).str.strip().str.upper().map(_TIER_ORD).astype(float)
    )
    df["def_tier_ord"] = (
        df["def_tier"].astype(str).str.strip().str.lower().map(_DEF_TIER_ORD).astype(float)
    )
    df["min_tier_ord"] = (
        df["minutes_tier"].astype(str).str.strip().str.lower().map(_MIN_TIER_ORD).astype(float)
    )
    df["line_bucket_ord"] = df["line_bucket"].map(_LINE_BUCKET_ORD).astype(float)
    df["over_flag"] = (direction.eq("OVER")).astype(float)

    # Drop broken-grade (sport, prop) groups before training so the meta-model
    # is not fitted to systematically miscoded outcomes.
    av = df["actual_value"].astype(str).str.strip()
    df["_zero_actual"] = av.isin(["0.0", "0", "0.00", ""]).astype(int)
    grade_q = (
        df.groupby(["sport_u", "prop_u"])["_zero_actual"].mean().reset_index()
        .rename(columns={"_zero_actual": "pct_actual_zero"})
    )
    df = df.merge(grade_q, on=["sport_u", "prop_u"], how="left")
    df = df[df["pct_actual_zero"] < 0.85].copy()
    df = df.drop(columns=["_zero_actual"], errors="ignore")
    return df


# ── feature matrix ─────────────────────────────────────────────────────────


def _top_props(bucket: pd.DataFrame, k: int = 8) -> list[str]:
    return (
        bucket["prop_u"]
        .value_counts()
        .head(k)
        .index.astype(str)
        .tolist()
    )


def _featurize(
    bucket: pd.DataFrame,
    *,
    top_props: list[str],
    role_levels: list[str],
    h2h_levels: list[str],
    gt_levels: list[str],
) -> pd.DataFrame:
    f = pd.DataFrame(index=bucket.index)
    f["ml_prob"] = bucket["ml_prob_n"].fillna(bucket["ml_prob_n"].median())
    f["line"] = bucket["line_num"].fillna(bucket["line_num"].median())
    f["tier_ord"] = bucket["tier_ord"].fillna(0.0)
    f["def_tier_ord"] = bucket["def_tier_ord"].fillna(2.0)
    f["min_tier_ord"] = bucket["min_tier_ord"].fillna(1.0)
    f["line_bucket_ord"] = bucket["line_bucket_ord"].fillna(2.0)
    f["over_flag"] = bucket["over_flag"].fillna(0.0)

    for tp in top_props:
        f[f"prop_is_{tp}"] = (bucket["prop_u"] == tp).astype(float)

    for r in role_levels:
        f[f"role_{r}"] = (bucket["role_tier"].astype(str).str.strip().str.upper() == r).astype(float)
    for h in h2h_levels:
        f[f"h2h_{h}"] = (bucket["h2h_bucket"].astype(str).str.strip().str.upper() == h).astype(float)
    for g in gt_levels:
        f[f"gt_{g}"] = (bucket["game_total_bucket"].astype(str).str.strip().str.upper() == g).astype(float)
    return f


# ── walk-forward training ──────────────────────────────────────────────────


def _walk_forward_predict(
    bucket: pd.DataFrame,
    *,
    backtest_since: str,
    min_train_n: int,
) -> pd.DataFrame:
    bucket = bucket.sort_values("_date").reset_index(drop=True)
    if "_date" not in bucket.columns or bucket.empty:
        return pd.DataFrame()

    top_props = _top_props(bucket, k=8)
    role_levels = [
        l
        for l, cnt in bucket["role_tier"].astype(str).str.strip().str.upper().value_counts().items()
        if cnt >= 30 and l not in {"", "NAN", "(MISSING)"}
    ][:5]
    h2h_levels = [
        l
        for l, cnt in bucket["h2h_bucket"].astype(str).str.strip().str.upper().value_counts().items()
        if cnt >= 30 and l not in {"", "NAN", "(MISSING)"}
    ][:5]
    gt_levels = [
        l
        for l, cnt in bucket["game_total_bucket"].astype(str).str.strip().str.upper().value_counts().items()
        if cnt >= 30 and l not in {"", "NAN", "(MISSING)"}
    ][:5]

    X_all = _featurize(
        bucket,
        top_props=top_props,
        role_levels=role_levels,
        h2h_levels=h2h_levels,
        gt_levels=gt_levels,
    )
    y_all = bucket["is_hit"].to_numpy(dtype=float)
    dates = bucket["_date"].to_numpy()
    unique_dates = sorted(set(d for d in dates if d >= backtest_since))

    out_rows: list[dict] = []
    for d in unique_dates:
        train_mask = dates < d
        test_mask = dates == d
        if train_mask.sum() < min_train_n or test_mask.sum() < MIN_PREDICT_N:
            continue
        Xtr = X_all.loc[train_mask].to_numpy(dtype=float)
        ytr = y_all[train_mask]
        if len(set(ytr.tolist())) < 2:
            continue
        Xte = X_all.loc[test_mask].to_numpy(dtype=float)

        base = LogisticRegression(max_iter=400, C=1.0)
        try:
            cal = CalibratedClassifierCV(estimator=base, method="isotonic", cv=3)
            cal.fit(Xtr, ytr)
            probs = cal.predict_proba(Xte)[:, 1]
        except Exception:
            base.fit(Xtr, ytr)
            probs = base.predict_proba(Xte)[:, 1]

        sub = bucket.loc[test_mask, ["_date", "sport_u", "pick_group", "prop_u", "tier", "ml_prob_n", "line", "is_hit"]].copy()
        sub["meta_prob"] = probs
        # Rank-based deciles: top 10% by predicted probability => decile 0,
        # bottom 10% => decile 9. Robust against tied probabilities (qcut+
        # duplicates="drop" was producing NaN labels when probs clustered).
        n_test = int(test_mask.sum())
        ranks = pd.Series(probs, index=sub.index).rank(method="first", ascending=False)
        sub["meta_decile"] = ((ranks - 1) * 10 // max(n_test, 1)).clip(0, 9).astype(int)
        out_rows.append(sub)

    return pd.concat(out_rows, ignore_index=True) if out_rows else pd.DataFrame()


def _backtest_summary(pred: pd.DataFrame) -> pd.DataFrame:
    if pred.empty:
        return pd.DataFrame()
    bucket_baseline = pred.groupby(["sport_u", "pick_group"])["is_hit"].mean().rename("base_hit_rate").reset_index()
    by_decile = (
        pred.groupby(["sport_u", "pick_group", "meta_decile"], dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
        .reset_index()
    )
    by_decile["hit_rate"] = by_decile["hits"] / by_decile["n"]
    by_decile["wilson_low"] = [
        _wilson_low(h, n) for h, n in zip(by_decile["hits"], by_decile["n"])
    ]
    by_decile = by_decile.merge(bucket_baseline, on=["sport_u", "pick_group"], how="left")
    by_decile["lift_vs_base"] = by_decile["hit_rate"] - by_decile["base_hit_rate"]
    return by_decile.sort_values(["sport_u", "pick_group", "meta_decile"])


# ── main ────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-04-15", help="Backtest dates >= this")
    ap.add_argument("--min-train-n", type=int, default=MIN_TRAIN_N)
    ap.add_argument("--buckets", nargs="*", help="Limit to e.g. 'NBA:Goblin' 'MLB:Demon'")
    args = ap.parse_args()

    raw = _load_all_graded()
    print(f"Loaded {len(raw):,} graded rows.")
    df = _prep(raw)
    print(f"Trusted decided rows: {len(df):,}")

    requested: set[tuple[str, str]] | None = None
    if args.buckets:
        requested = set()
        for spec in args.buckets:
            sp, _, pg = spec.partition(":")
            if not sp or not pg:
                continue
            requested.add((sp.upper(), pg))

    summaries: list[pd.DataFrame] = []
    for (sport, pg), bucket in df.groupby(["sport_u", "pick_group"], dropna=False):
        if requested is not None and (sport, pg) not in requested:
            continue
        if len(bucket) < args.min_train_n + 200:
            continue
        print(f"\n=== {sport} {pg}  rows={len(bucket):,} ===")
        pred = _walk_forward_predict(
            bucket,
            backtest_since=args.since,
            min_train_n=args.min_train_n,
        )
        if pred.empty:
            print("  no predictions (insufficient data after walk-forward gating)")
            continue
        out_path = OUT_DIR / f"{sport}_{pg.replace(' ', '_')}_backtest.csv"
        pred.to_csv(out_path, index=False)
        summary = _backtest_summary(pred)
        summaries.append(summary)
        pd.set_option("display.width", 220)
        pd.set_option("display.max_columns", 30)
        pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))
        cols = ["meta_decile", "n", "hits", "hit_rate", "wilson_low", "base_hit_rate", "lift_vs_base"]
        print(summary[cols].to_string(index=False))
        print(f"  -> {out_path}")

    if summaries:
        big = pd.concat(summaries, ignore_index=True)
        big.to_csv(OUT_DIR / "summary.csv", index=False)
        # Top-decile hit rate summary across all buckets:
        top = big[big["meta_decile"] == 0].copy()
        print("\n=== TOP-DECILE HIT RATE PER BUCKET ===")
        print(
            top[["sport_u", "pick_group", "n", "hit_rate", "wilson_low", "base_hit_rate", "lift_vs_base"]]
            .sort_values("lift_vs_base", ascending=False)
            .to_string(index=False)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
