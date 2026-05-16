"""Apply Layer-1 lift gates as a tier override.

Reads outputs/winners_by_pick_type/lift_recommendations.csv (produced by
scripts/lift_finder.py) and applies the best single-feature gate per
(sport, pick_group) to a graded_props_<date>.json or a step8 slate xlsx.

Output: adds two columns / fields per prop:
  - gate_rule          : the rule applied, or "(no gate)" / "(low confidence bucket)"
  - tier_override      : one of A / B / C / D, derived only from the gate
                         (separate from the model's own `tier`)

Backtest mode (--backtest --since 2026-04-01) re-grades all available
graded_props JSONs and reports the lift in hit rate when filtering to
override == "A" vs the bucket baseline.

Usage:
    python scripts/apply_tier_overrides.py --date 2026-05-08
    python scripts/apply_tier_overrides.py --backtest --since 2026-04-01
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
LIFT_CSV = REPO / "outputs" / "winners_by_pick_type" / "lift_recommendations.csv"


# Gates we'll apply as tier_override == "A" (the strict, premium-confidence bucket).
# Gates we'll apply as "B" use a lower Wilson lower bound. The remainder of the
# bucket gets "C" if the bucket as a whole is profitable, otherwise "D".
A_WILSON_FLOOR = 0.55
B_WILSON_FLOOR = 0.50
PROFITABLE_BUCKET_FLOOR = 0.50

# Minimum sample sizes — refuse to apply a gate built on too thin a slice.
MIN_GATE_N = 100


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
    if x != x:  # NaN
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


def _pick_base(s: str) -> str:
    v = str(s or "").strip().lower()
    if v == "goblin":
        return "Goblin"
    if v == "demon":
        return "Demon"
    return "Standard"


def _pick_group(pick_type: str, direction: str) -> str:
    base = _pick_base(pick_type)
    d = str(direction or "").strip().upper()
    if base != "Standard":
        return base
    if d == "OVER":
        return "Standard OVER"
    if d == "UNDER":
        return "Standard UNDER"
    return "Standard (no dir)"


def _normalize_cat(v) -> str:
    s = str(v or "").strip()
    if not s or s.lower() in {"nan", "none", "null", "(missing)", "—", "-", "–"}:
        return "(missing)"
    return s


def _row_feature(row: dict, feature: str) -> str:
    if feature == "line_bucket":
        return _line_bucket(row.get("line"))
    if feature == "ml_prob":
        try:
            return float(row.get("ml_prob") or float("nan"))
        except (TypeError, ValueError):
            return float("nan")
    return _normalize_cat(row.get(feature))


def _gate_match(row: dict, feature: str, rule: str) -> bool:
    """Return True if the row satisfies the lift_recommendation rule."""
    val = _row_feature(row, feature)
    rule_str = str(rule or "")
    if "==" in rule_str:
        right = rule_str.split("==", 1)[1].strip().strip('"').strip("'")
        return str(val) == right
    if ">=" in rule_str:
        right = rule_str.split(">=", 1)[1].strip()
        try:
            r = float(right)
        except ValueError:
            return False
        try:
            v = float(val)
        except (TypeError, ValueError):
            return False
        return v == v and v >= r
    return False


def load_lift_table() -> pd.DataFrame:
    if not LIFT_CSV.is_file():
        raise SystemExit(
            f"Missing {LIFT_CSV}. Run scripts/lift_finder.py first."
        )
    df = pd.read_csv(LIFT_CSV)
    keep_cols = {"sport", "pick_group", "base_n", "base_hit_rate", "feature",
                 "rule", "n", "hits", "hit_rate", "wilson_low",
                 "lift_vs_base", "kept_frac"}
    df = df[[c for c in df.columns if c in keep_cols]].copy()
    df = df[df["n"] >= MIN_GATE_N].copy()
    return df


def _best_gate_per_bucket(lift: pd.DataFrame) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for (sport, pg), g in lift.groupby(["sport", "pick_group"]):
        # Drop the leaky `edge` feature defensively; lift_finder excludes it,
        # but stale CSVs may still contain it.
        g = g[g["feature"].astype(str) != "edge"]
        if g.empty:
            continue
        g = g.sort_values("wilson_low", ascending=False)
        top = g.iloc[0]
        out[(sport, pg)] = {
            "feature": str(top["feature"]),
            "rule": str(top["rule"]),
            "wilson_low": float(top["wilson_low"]),
            "hit_rate": float(top["hit_rate"]),
            "lift_vs_base": float(top["lift_vs_base"]),
            "n": int(top["n"]),
            "base_hit_rate": float(top["base_hit_rate"]),
            "base_n": int(top["base_n"]),
        }
    return out


def _override_tier(matched: bool, gate: dict | None, base_hr: float) -> str:
    if gate is None:
        # No usable gate. Fall back to whether the bucket is profitable overall.
        return "C" if base_hr >= PROFITABLE_BUCKET_FLOOR else "D"
    if matched:
        if gate["wilson_low"] >= A_WILSON_FLOOR and gate["lift_vs_base"] >= 0.04:
            return "A"
        if gate["wilson_low"] >= B_WILSON_FLOOR:
            return "B"
        return "C"
    # Did not match the gate. The remaining fraction of the bucket is the residual:
    # often it's still profitable for Goblin and Standard UNDER. Use bucket base hr.
    return "C" if base_hr >= PROFITABLE_BUCKET_FLOOR else "D"


def annotate(df: pd.DataFrame, gates: dict) -> pd.DataFrame:
    df = df.copy()
    df["sport_u"] = df["sport"].astype(str).str.upper().str.strip()
    df["pick_group"] = [
        _pick_group(pt, d)
        for pt, d in zip(df.get("pick_type", ""), df.get("direction", ""))
    ]

    def _annotate_row(r) -> tuple[str, str, bool]:
        key = (r["sport_u"], r["pick_group"])
        gate = gates.get(key)
        if gate is None:
            return (
                "(no gate)",
                _override_tier(False, None, 0.0),
                False,
            )
        matched = _gate_match(r.to_dict(), gate["feature"], gate["rule"])
        rule_text = gate["rule"] if matched else f"NOT ({gate['rule']})"
        return (
            rule_text,
            _override_tier(matched, gate, gate["base_hit_rate"]),
            matched,
        )

    out = df.apply(_annotate_row, axis=1, result_type="expand")
    out.columns = ["gate_rule", "tier_override", "gate_matched"]
    return pd.concat([df, out], axis=1)


def _load_graded(date_str: str) -> pd.DataFrame:
    candidates = [
        REPO / "ui_runner" / "templates" / f"graded_props_{date_str}.json",
        REPO / "mobile" / "www" / f"graded_props_{date_str}.json",
    ]
    src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        return pd.DataFrame()
    payload = json.loads(src.read_text(encoding="utf-8"))
    df = pd.DataFrame(payload.get("props") or [])
    df["_src_path"] = str(src)
    return df


def _save_graded_overrides(date_str: str, df: pd.DataFrame) -> Path:
    out_dir = REPO / "outputs" / "tier_overrides"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tier_override_{date_str}.csv"
    keep = [
        "sport", "player", "team", "opp_team", "prop", "line", "direction",
        "pick_type", "tier", "ml_prob", "result", "gate_rule",
        "tier_override", "gate_matched",
    ]
    keep = [c for c in keep if c in df.columns]
    df[keep].to_csv(out, index=False)
    return out


def _backtest(since: str, gates: dict) -> None:
    files = sorted((REPO / "ui_runner" / "templates").glob("graded_props_*.json"))
    rows = []
    for f in files:
        date_str = f.stem.replace("graded_props_", "")
        if date_str < since:
            continue
        df = _load_graded(date_str)
        if df.empty:
            continue
        df = annotate(df, gates)
        df["_date"] = date_str
        rows.append(df)
    if not rows:
        print("No graded JSONs in range.")
        return
    big = pd.concat(rows, ignore_index=True)
    res = big["result"].astype(str).str.upper()
    big = big[res.isin(["HIT", "MISS"])].copy()
    big["is_hit"] = (res.loc[big.index] == "HIT").astype(int)

    print(f"\nBacktest range: {since} -> {sorted(big['_date'].unique())[-1]}  rows={len(big):,}\n")

    by_bucket = (
        big.groupby(["sport_u", "pick_group", "tier_override"], dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
        .reset_index()
    )
    by_bucket["hit_rate"] = by_bucket["hits"] / by_bucket["n"]
    by_bucket["wilson_low"] = [
        _wilson_low(h, n) for h, n in zip(by_bucket["hits"], by_bucket["n"])
    ]
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))
    print("=== HIT RATE BY (sport, pick_group, tier_override) ===")
    print(by_bucket.sort_values(["sport_u", "pick_group", "tier_override"]).to_string(index=False))

    overall = (
        big.groupby("tier_override")
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
    )
    overall["hit_rate"] = overall["hits"] / overall["n"]
    print("\n=== HIT RATE BY tier_override OVERALL ===")
    print(overall.to_string())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD: annotate graded_props for this date")
    ap.add_argument("--backtest", action="store_true",
                    help="Apply gates to every graded_props_*.json since --since and report aggregated lift")
    ap.add_argument("--since", default="2026-04-01")
    args = ap.parse_args()

    lift = load_lift_table()
    gates = _best_gate_per_bucket(lift)
    print(f"Loaded {len(gates)} (sport, pick_group) gates from {LIFT_CSV}")

    if args.backtest:
        _backtest(args.since, gates)
        return 0

    if not args.date:
        ap.error("Provide --date YYYY-MM-DD or --backtest")
    df = _load_graded(args.date)
    if df.empty:
        print(f"No graded_props for {args.date}")
        return 1
    df = annotate(df, gates)
    out = _save_graded_overrides(args.date, df)
    print(f"Wrote {out}  rows={len(df):,}")

    summary = (
        df[df["result"].astype(str).str.upper().isin(["HIT", "MISS"])]
        .assign(is_hit=lambda x: (x["result"].astype(str).str.upper() == "HIT").astype(int))
        .groupby(["sport_u", "pick_group", "tier_override"], dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"))
    )
    summary["hit_rate"] = summary["hits"] / summary["n"]
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda x: f"{x:0.4f}" if isinstance(x, float) else str(x))
    print("\n=== TODAY'S HIT RATE BY tier_override (decided rows) ===")
    print(summary.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
