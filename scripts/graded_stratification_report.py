#!/usr/bin/env python3
"""
Weekly / post-retrain: walk graded XLSX discovery tree, emit stratified CSV + HTML.

Tier 1: hit rates by (sport, prop, direction, pick_type, line_bucket) plus
context_known / defense_known / minutes_known. Goblin/Demon UNDER rows are
dropped (invalid market side).

Calibration: per (sport, direction, pick_type) with n>=100, decile bins on
ml_prob vs result_binary; flag bins where |mean_pred - mean_true| > 0.08.

Default roots: <repo>/ui_runner/graded_slate and <repo>/outputs.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from graded_line_quality_features import (  # noqa: E402
    STRAT_FEATURE_COLUMNS,
    add_stratification_columns,
    line_bucket,
)

try:
    from sklearn.calibration import calibration_curve
except ImportError:
    calibration_curve = None  # type: ignore[misc, assignment]

try:
    from analyze_graded_prop_winners import load_unified, normalize_decided  # type: ignore[import-not-found]
except ImportError:
    load_unified = None  # type: ignore[misc, assignment]
    normalize_decided = None  # type: ignore[misc, assignment]

try:
    from edge_predict_utils import (  # type: ignore[import-not-found]
        augment_graded_box_raw_for_edge,
        graded_filename_sport_to_train_sport,
    )
except ImportError:
    augment_graded_box_raw_for_edge = None  # type: ignore[misc, assignment]
    graded_filename_sport_to_train_sport = None  # type: ignore[misc, assignment]

try:
    from edge_feature_engineering import build_feature_vector  # type: ignore[import-not-found]
except ImportError:
    build_feature_vector = None  # type: ignore[misc, assignment]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sport_display(s: str) -> str:
    u = str(s or "").strip().lower()
    mapping = {
        "nba": "NBA",
        "nhl": "NHL",
        "mlb": "MLB",
        "soccer": "Soccer",
        "football": "Soccer",
        "cbb": "CBB",
        "wcbb": "CBB",
        "wnba": "WNBA",
        "tennis": "Tennis",
        "nba1h": "NBA1H",
        "nba1q": "NBA1Q",
    }
    return mapping.get(u, str(s or "").strip().upper() or "UNKNOWN")


def _line_series(df: pd.DataFrame) -> pd.Series:
    for c in ("line_score", "line", "Line", "LINE"):
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _home_away_series(df: pd.DataFrame) -> pd.Series:
    for c in ("home_away", "home_away_flag", "is_home", "venue_side", "HA"):
        if c not in df.columns:
            continue
        s = df[c].astype(str).str.strip().str.upper()
        s = s.replace({"NAN": "", "NONE": "", "NULL": ""})
        if s.notna().any() and (s != "").any():
            return s.mask(s.eq(""), "(missing)")
    return pd.Series("(not in graded export)", index=df.index, dtype=str)


def _pick_type_norm(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower()


def _attach_features_via_build_vector(decided: pd.DataFrame) -> pd.DataFrame:
    """Adds line_bucket*, context_known, defense_known, minutes_known when stack is present."""
    if (
        build_feature_vector is None
        or augment_graded_box_raw_for_edge is None
        or graded_filename_sport_to_train_sport is None
    ):
        out = decided.copy()
        ls = _line_series(out)
        out["line_bucket"] = ls.map(line_bucket).astype(str)
        enc = {
            "micro": 0.0,
            "low": 1.0,
            "mid": 2.0,
            "high": 3.0,
            "xl": 4.0,
            "(missing)": -1.0,
        }
        out["line_bucket_encoded"] = out["line_bucket"].map(enc).astype(float)
        pick_raw = out.get("pick_type", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
        out["context_known"] = (~pick_raw.isin(["", "NAN", "(MISSING)"])).astype(float)
        def_raw = out.get("def_tier", pd.Series(np.nan, index=out.index))
        sdef = def_raw.astype(str).str.strip().str.upper()
        bad = {"", "NAN", "(MISSING)", "UNKNOWN", "NEUTRAL"}
        out["defense_known"] = (~sdef.isin(bad) & def_raw.notna()).astype(float)
        mt = out.get("minutes_tier", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
        out["minutes_known"] = mt.isin(["HIGH", "MEDIUM", "LOW"]).astype(float)
        return out

    parts: list[pd.DataFrame] = []
    for sp in sorted(decided["_sport"].dropna().unique(), key=str):
        m = decided["_sport"] == sp
        sub = decided.loc[m].copy()
        orig_sub = sub.copy()
        aug = augment_graded_box_raw_for_edge(sub)
        model_sp = graded_filename_sport_to_train_sport(str(sp))
        built = build_feature_vector(aug, model_sp)
        merged = add_stratification_columns(built, orig_sub)
        keep = ["line_bucket", *STRAT_FEATURE_COLUMNS]
        keep = [c for c in keep if c in merged.columns]
        parts.append(merged[keep])
    feat = pd.concat(parts, axis=0)
    # Graded exports may already include e.g. line_bucket; drop overlaps before join.
    overlap = [c for c in feat.columns if c in decided.columns]
    if overlap:
        decided = decided.drop(columns=overlap, errors="ignore")
    return decided.join(feat, how="left")


def _drop_invalid_goblin_demon_under(df: pd.DataFrame) -> pd.DataFrame:
    pt = _pick_type_norm(df.get("pick_type", pd.Series("", index=df.index)))
    d = df.get("direction", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()
    bad = pt.isin(["goblin", "demon"]) & d.eq("UNDER")
    return df.loc[~bad].copy()


def _result_binary(s: pd.Series) -> pd.Series:
    u = s.astype(str).str.strip().str.upper()
    return pd.Series(np.where(u == "HIT", 1.0, np.where(u == "MISS", 0.0, np.nan)), index=s.index)


def _calibration_rows(
    df: pd.DataFrame,
    *,
    min_stratum: int,
    flag_eps: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (all_bins, flagged_bins)."""
    rows: list[dict[str, object]] = []
    flagged: list[dict[str, object]] = []
    if calibration_curve is None or df.empty or "ml_prob" not in df.columns:
        return pd.DataFrame(rows), pd.DataFrame(flagged)

    need = ["result_binary", "ml_prob", "sport_disp", "direction", "pick_type"]
    for c in need:
        if c not in df.columns:
            return pd.DataFrame(rows), pd.DataFrame(flagged)

    sub = df[df["result_binary"].notna() & df["ml_prob"].notna()].copy()
    sports = ["NBA", "NHL", "MLB", "Soccer"]
    for sp in sports:
        for pick_type in ["standard", "goblin", "demon"]:
            mask = (sub["sport_disp"] == sp) & (_pick_type_norm(sub["pick_type"]) == pick_type)
            slice_df = sub.loc[mask]
            if len(slice_df) < min_stratum:
                continue
            for direction in sorted(slice_df["direction"].astype(str).str.upper().unique()):
                dmask = slice_df["direction"].astype(str).str.upper().eq(direction)
                ddf = slice_df.loc[dmask]
                if len(ddf) < min_stratum:
                    continue
                y = ddf["result_binary"].to_numpy(dtype=float)
                p = ddf["ml_prob"].to_numpy(dtype=float)
                try:
                    prob_true, prob_pred = calibration_curve(y, p, n_bins=10, strategy="quantile")
                except TypeError:
                    prob_true, prob_pred = calibration_curve(y, p, n_bins=10)
                for i, (t, pr) in enumerate(zip(prob_true, prob_pred)):
                    gap = abs(float(pr) - float(t))
                    row = {
                        "sport": sp,
                        "direction": direction,
                        "pick_type": pick_type,
                        "bin_index": i,
                        "mean_pred": float(pr),
                        "mean_true": float(t),
                        "gap": gap,
                        "n_stratum": len(ddf),
                    }
                    rows.append(row)
                    if gap > flag_eps:
                        flagged.append({**row, "flagged": True})
    return pd.DataFrame(rows), pd.DataFrame(flagged)


def _html_escape_df(tab: pd.DataFrame, max_rows: int = 50) -> str:
    if tab.empty:
        return "<p>(no rows)</p>"
    t = tab.head(max_rows).copy()
    th = "".join(f"<th>{html.escape(str(c))}</th>" for c in t.columns)
    body_rows: list[str] = []
    for _, r in t.iterrows():
        cells = "".join(f"<td>{html.escape(str(v))}</td>" for v in r)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        "<table class='grid'><thead><tr>"
        + th
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def run(
    roots: list[Path],
    out_dir: Path,
    *,
    min_cell_n: int,
    min_cal_stratum: int,
    cal_flag_eps: float,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if load_unified is None or normalize_decided is None:
        raise SystemExit(
            "Missing analyze_graded_prop_winners.py on PYTHONPATH; "
            "place it next to this script under scripts/."
        )

    raw = load_unified(roots)
    decided = normalize_decided(raw)
    decided = _drop_invalid_goblin_demon_under(decided)
    if decided.empty:
        print("No decided graded rows after filters.")
        return

    decided["sport_disp"] = decided["_sport"].map(_sport_display)
    decided["home_away"] = _home_away_series(decided)
    decided = _attach_features_via_build_vector(decided)

    if "result" in decided.columns:
        decided["result_binary"] = _result_binary(decided["result"])
    else:
        decided["result_binary"] = decided.get("is_hit", np.nan)

    gcols = ["sport_disp", "prop", "direction", "pick_type", "line_bucket"]
    gcols = [c for c in gcols if c in decided.columns]
    hit_tbl = (
        decided.groupby(gcols, dropna=False)
        .agg(
            n=("is_hit", "size"),
            hits=("is_hit", "sum"),
            mean_ml=("ml_prob", "mean"),
            context_known_rate=("context_known", "mean"),
            defense_known_rate=("defense_known", "mean"),
            minutes_known_rate=("minutes_known", "mean"),
        )
        .reset_index()
    )
    hit_tbl["hit_rate"] = hit_tbl["hits"] / hit_tbl["n"]
    hit_tbl = hit_tbl[hit_tbl["n"] >= min_cell_n].sort_values(
        ["sport_disp", "n"], ascending=[True, False]
    )

    path_main = out_dir / "graded_strat_hit_rates.csv"
    hit_tbl.to_csv(path_main, index=False)

    cal_all, cal_flag = _calibration_rows(
        decided,
        min_stratum=min_cal_stratum,
        flag_eps=cal_flag_eps,
    )
    cal_all.to_csv(out_dir / "graded_calibration_bins.csv", index=False)
    cal_flag.to_csv(out_dir / "graded_calibration_flagged.csv", index=False)

    tier_a = hit_tbl[
        (hit_tbl["context_known_rate"] >= 0.99)
        & (hit_tbl["defense_known_rate"] >= 0.99)
        & (hit_tbl["minutes_known_rate"] >= 0.99)
    ].copy()
    tier_a.to_csv(out_dir / "graded_strat_tier_a_slice.csv", index=False)

    ha_vc = (
        decided.groupby(["sport_disp", "home_away"], dropna=False)
        .size()
        .reset_index(name="n")
        .sort_values(["sport_disp", "n"], ascending=[True, False])
    )
    ha_vc.to_csv(out_dir / "graded_home_away_by_sport.csv", index=False)

    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ha_note = (
        "home_away from graded columns when present (home_away, is_home, venue_side, …); "
        "otherwise tagged (not in graded export)."
    )
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Graded stratification report</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #111; }}
h1,h2 {{ font-weight: 600; }}
.grid {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
.grid th, .grid td {{ border: 1px solid #ccc; padding: 0.35rem 0.5rem; text-align: left; }}
.grid th {{ background: #f4f4f4; }}
.note {{ color: #444; font-size: 0.9rem; max-width: 52rem; }}
</style>
</head>
<body>
<h1>Graded stratification report</h1>
<p class="note">Generated {html.escape(gen_at)}. Tier A requires context_known, defense_known, and minutes_known near 1.0 on the slice (unknown-context props excluded).</p>
<p class="note">Goblin/Demon UNDER rows removed. {html.escape(ha_note)}</p>

<h2>Primary stratification (min n = {min_cell_n})</h2>
{_html_escape_df(hit_tbl)}

<h2>Calibration bins flagged (|pred - true| &gt; {cal_flag_eps})</h2>
{_html_escape_df(cal_flag)}

<h2>Tier A (all three known-rates ≥ 0.99)</h2>
{_html_escape_df(tier_a, max_rows=40)}

<h2>Tier 2 (blocked)</h2>
<p class="note">Ticket leg structure (n_legs, prob_std, all_over, dominant_sport) is deferred until graded ticket set exceeds 500 rows.</p>
</body>
</html>
"""
    (out_dir / "graded_stratification_report.html").write_text(html_doc, encoding="utf-8")

    print(f"Wrote {path_main} rows={len(hit_tbl)}")
    print(f"Wrote calibration: {out_dir / 'graded_calibration_bins.csv'} flagged={len(cal_flag)}")
    print(f"Wrote {out_dir / 'graded_stratification_report.html'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/data/reports/graded_stratification)",
    )
    ap.add_argument(
        "--roots",
        type=Path,
        nargs="*",
        default=None,
        help="Extra roots to scan for graded_*.xlsx (defaults: ui_runner/graded_slate, outputs)",
    )
    ap.add_argument("--min-cell-n", type=int, default=15, help="Min rows per stratification cell")
    ap.add_argument("--min-cal-stratum", type=int, default=100, help="Min rows for calibration_curve slice")
    ap.add_argument("--cal-flag-eps", type=float, default=0.08, help="Flag calibration bin if gap exceeds this")
    args = ap.parse_args()
    root = _repo_root()
    roots = list(args.roots) if args.roots else []
    for rel in (root / "ui_runner" / "graded_slate", root / "outputs"):
        if rel not in roots:
            roots.append(rel)
    out_dir = args.out_dir or (root / "data" / "reports" / "graded_stratification")
    run(roots, out_dir, min_cell_n=args.min_cell_n, min_cal_stratum=args.min_cal_stratum, cal_flag_eps=args.cal_flag_eps)


if __name__ == "__main__":
    main()
