"""Apply strict-mode signals (Layer-1 gates + meta-classifier) to a graded slate.

Reads a graded_props_<date>.json, attaches:
  * tier_override   — derived from scripts/lift_finder.py recommendations
  * gate_rule       — the matched layer-1 rule (or "(no gate)")
  * meta_prob       — per-bucket classifier P(hit) (when a model exists)
  * meta_decile     — top-decile=0 ranking inside its (sport, pick_group) bucket
  * strict_label    — combined "PREMIUM" / "STRONG" / "STANDARD" / "AVOID" tag

This script is the integration point between the analysis and the slate-eval
HTML. It writes:
  outputs/strict_mode/strict_<date>.csv             (full per-row table)
  outputs/strict_mode/strict_<date>_summary.json    (counts + hit-rate snapshot)

Usage
    python scripts/apply_strict_mode.py --date 2026-05-08
    python scripts/apply_strict_mode.py --date 2026-05-08 --train-since 2026-04-15
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import warnings

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
import sys

if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from apply_tier_overrides import (  # noqa: E402
    annotate as apply_overrides,
    load_lift_table,
    _best_gate_per_bucket,
    _load_graded,
)
from train_meta_classifier import (  # noqa: E402
    _prep as meta_prep,
    _walk_forward_predict,
    _load_all_graded as _load_all_for_meta,
)

OUT_DIR = REPO / "outputs" / "strict_mode"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _strict_label(row) -> str:
    override = str(row.get("tier_override", "D"))
    decile = row.get("meta_decile")
    has_meta = pd.notna(decile)
    if override == "A" and (not has_meta or int(decile) <= 1):
        return "PREMIUM"
    if override == "A":
        return "STRONG"
    if override == "B" and has_meta and int(decile) <= 2:
        return "STRONG"
    if override == "B":
        return "STANDARD"
    if override == "C" and has_meta and int(decile) <= 1:
        return "STANDARD"
    if override == "D":
        return "AVOID"
    return "STANDARD"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    ap.add_argument(
        "--train-since",
        default="2026-02-19",
        help="Earliest date to use as classifier training data (default: full history)",
    )
    ap.add_argument(
        "--min-bucket-n",
        type=int,
        default=400,
        help="Minimum trusted training rows for a bucket to receive meta_prob",
    )
    args = ap.parse_args()

    raw = _load_graded(args.date)
    if raw.empty:
        print(f"No graded_props for {args.date}")
        return 1

    lift = load_lift_table()
    gates = _best_gate_per_bucket(lift)
    annotated = apply_overrides(raw, gates)

    history = _load_all_for_meta()
    history = history[history["_date"] < args.date].copy()
    history = history[history["_date"] >= args.train_since]
    history = meta_prep(history)

    # Append today's slate (target) so featurization shares feature columns.
    today_prep = meta_prep(annotated.assign(_date=args.date))
    big = pd.concat([history, today_prep], ignore_index=True)

    pred_rows: list[pd.DataFrame] = []
    for (sport, pg), bucket in big.groupby(["sport_u", "pick_group"], dropna=False):
        train = bucket[bucket["_date"] < args.date]
        test = bucket[bucket["_date"] == args.date]
        if test.empty:
            continue
        if len(train) < args.min_bucket_n:
            continue
        pred = _walk_forward_predict(
            bucket,
            backtest_since=args.date,
            min_train_n=args.min_bucket_n,
        )
        if pred.empty:
            continue
        pred_rows.append(pred)

    meta_df = pd.concat(pred_rows, ignore_index=True) if pred_rows else pd.DataFrame()

    # Merge meta predictions back onto the annotated slate using a stable key.
    if not meta_df.empty:
        # Use (sport_u, pick_group, prop_u, line, ml_prob_n, _date) as join key —
        # prop_u + line + ml_prob is unique per slate row in practice.
        keep_cols = ["sport_u", "pick_group", "prop_u", "line", "ml_prob_n", "_date", "meta_prob", "meta_decile"]
        meta_df = meta_df[keep_cols].drop_duplicates(
            subset=["sport_u", "pick_group", "prop_u", "line", "ml_prob_n", "_date"]
        )
        meta_df["meta_decile"] = meta_df["meta_decile"].astype("Int64")
        annotated_join = annotated.copy()
        annotated_join["_date"] = args.date
        annotated_join["ml_prob_n"] = pd.to_numeric(annotated_join["ml_prob"], errors="coerce")
        annotated_join["prop_u"] = annotated_join["prop"].astype(str).str.strip()
        annotated_join["line_n"] = pd.to_numeric(annotated_join["line"], errors="coerce")
        meta_df["line_n"] = pd.to_numeric(meta_df["line"], errors="coerce")
        annotated_join = annotated_join.merge(
            meta_df.drop(columns=["line"]),
            on=["sport_u", "pick_group", "prop_u", "line_n", "ml_prob_n", "_date"],
            how="left",
        )
        annotated = annotated_join

    annotated["strict_label"] = annotated.apply(_strict_label, axis=1)

    keep_for_csv = [
        "sport", "player", "team", "opp_team", "prop", "line", "direction",
        "pick_type", "tier", "ml_prob", "tier_override", "gate_rule",
        "meta_prob", "meta_decile", "strict_label", "result",
    ]
    keep_for_csv = [c for c in keep_for_csv if c in annotated.columns]
    out_csv = OUT_DIR / f"strict_{args.date}.csv"
    annotated[keep_for_csv].to_csv(out_csv, index=False)

    # Summary: count + hit rate by strict_label, conditional on decided rows.
    decided = annotated[annotated["result"].astype(str).str.upper().isin(["HIT", "MISS"])].copy()
    decided["is_hit"] = (decided["result"].astype(str).str.upper() == "HIT").astype(int)
    summary = (
        annotated.groupby("strict_label")
        .agg(slate_count=("strict_label", "size"))
        .reset_index()
    )
    if not decided.empty:
        decided_summary = (
            decided.groupby("strict_label")
            .agg(decided_n=("is_hit", "size"), hits=("is_hit", "sum"))
            .reset_index()
        )
        decided_summary["hit_rate"] = decided_summary["hits"] / decided_summary["decided_n"]
        summary = summary.merge(decided_summary, on="strict_label", how="left")

    out_json = OUT_DIR / f"strict_{args.date}_summary.json"
    out_json.write_text(json.dumps(summary.to_dict(orient="records"), indent=2), encoding="utf-8")
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))
    print(f"Wrote {out_csv} ({len(annotated):,} rows)")
    print(f"Wrote {out_json}")
    print("\n=== STRICT-MODE LABEL SNAPSHOT ===")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
