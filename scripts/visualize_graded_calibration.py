#!/usr/bin/env python3
"""
Per-sport graded prop analysis: rank numeric fields by correlation with hits
and quintile lift; save heatmaps, bar charts, and a CSV summary.

Uses the same Box Raw aggregation as analyze_graded_ml_calibration.py.

Example:
  py -3.14 scripts/visualize_graded_calibration.py --out-dir outputs/calibration_viz --exclude-demon

Outputs (default: outputs/calibration_viz/):
  - sport_feature_metrics_*.csv — full grid: sport x feature with r_pb, p, quintile_lift
  - heatmap_point_biserial_*.png — which fields align with wins per sport
  - heatmap_quintile_lift_*.png — how much win rate spreads across quintiles
  - bars_top_features_*.png — top |r| features per sport
  - bars_edge_quintiles_*.png — win rate by edge bucket (NBA/CBB/NHL/Soccer/MLB)
  - scatter_r_vs_lift_*.png — |r| vs lift (top-right = strongest stackable signal)

Requires: pandas, numpy, matplotlib; scipy optional (exact p-values; else r only).
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

try:
    from scipy import stats
except ImportError:  # pragma: no cover
    stats = None

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from analyze_graded_ml_calibration import (  # noqa: E402
    _repo_root,
    load_box_raw_decided,
)

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as e:
    raise SystemExit(
        "matplotlib is required. Install: py -m pip install matplotlib\n" + str(e)
    ) from e


_NUMERIC_FEATURES = [
    "edge",
    "rank_score",
    "projection",
    "proj_minus_line",
    "abs_edge",
    "line",
    "ml_prob",
    "ml_edge",
    "edge_score",
    "blended_score",
]


def _point_biserial(y: np.ndarray, x: np.ndarray) -> tuple[float, float, int]:
    m = np.isfinite(x) & np.isfinite(y)
    n = int(m.sum())
    if n < 30:
        return float("nan"), float("nan"), n
    yy = y[m].astype(float)
    xx = x[m].astype(float)
    if stats is not None:
        r, p = stats.pointbiserialr(yy, xx)
        return float(r), float(p), n
    if np.std(yy) < 1e-12 or np.std(xx) < 1e-12:
        return float("nan"), float("nan"), n
    r = float(np.corrcoef(yy, xx)[0, 1])
    return r, float("nan"), n


def _quintile_lift(hit: pd.Series, x: pd.Series, q: int = 5) -> float:
    m = hit.notna() & x.notna() & np.isfinite(pd.to_numeric(x, errors="coerce"))
    if int(m.sum()) < 80:
        return float("nan")
    xd = pd.to_numeric(x[m], errors="coerce")
    hd = hit[m].astype(float)
    try:
        bins = pd.qcut(xd, q=q, duplicates="drop")
    except (ValueError, TypeError):
        return float("nan")
    g = hd.groupby(bins, observed=True).mean()
    if len(g) < 2:
        return float("nan")
    return float(g.max() - g.min())


def _prepare_frame(root: Path, exclude_demon: bool, demon_only: bool) -> tuple[pd.DataFrame, str]:
    df = load_box_raw_decided(root)
    note = "all"
    if exclude_demon and demon_only:
        raise ValueError("use only one of --exclude-demon / --demon-only")
    if exclude_demon:
        df = df[~df["is_demon"]].copy()
        note = "non_demon"
    elif demon_only:
        df = df[df["is_demon"]].copy()
        note = "demon_only"
    return df, note


def build_metrics(
    decided: pd.DataFrame,
    min_n_sport: int,
) -> pd.DataFrame:
    rows: list[dict] = []
    sports = sorted(decided["_sport"].dropna().unique(), key=lambda s: str(s))
    for sp in sports:
        sub = decided[decided["_sport"] == sp]
        sub = sub[sub["is_hit"].notna()]
        if len(sub) < min_n_sport:
            continue
        baseline = float(sub["is_hit"].mean())
        y = sub["is_hit"].values.astype(float)
        for feat in _NUMERIC_FEATURES:
            if feat not in sub.columns:
                continue
            x = pd.to_numeric(sub[feat], errors="coerce").values
            r_pb, p, n = _point_biserial(y, x)
            ql = _quintile_lift(sub["is_hit"], sub[feat])
            rows.append(
                {
                    "sport": sp,
                    "feature": feat,
                    "n": n,
                    "baseline_hit_rate": baseline,
                    "point_biserial_r": r_pb,
                    "p_value": p,
                    "quintile_lift": ql,
                    "abs_r": abs(r_pb) if np.isfinite(r_pb) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def plot_correlation_heatmap(metrics: pd.DataFrame, out_path: Path) -> None:
    if metrics.empty:
        return
    pivot = metrics.pivot_table(index="sport", columns="feature", values="point_biserial_r", aggfunc="first")
    pivot = pivot.reindex(columns=[c for c in _NUMERIC_FEATURES if c in pivot.columns])
    if pivot.empty or pivot.shape[0] == 0:
        return

    fig, ax = plt.subplots(figsize=(max(10, pivot.shape[1] * 0.9), max(4, pivot.shape[0] * 0.55)))
    im = ax.imshow(pivot.values.astype(float), aspect="auto", cmap="RdBu_r", vmin=-0.35, vmax=0.35)
    ax.set_xticks(range(pivot.shape[1]))
    ax.set_xticklabels(pivot.columns, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(pivot.shape[0]))
    ax.set_yticklabels(pivot.index.astype(str), fontsize=10)
    ax.set_title("Point-biserial r: feature vs hit (HIT=1, MISS=0)\nPositive => higher feature values associate with wins")
    plt.colorbar(im, ax=ax, fraction=0.03, pad=0.04, label="r_pb")
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="black" if abs(v) < 0.18 else "white", fontsize=7)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_quintile_lift_heatmap(metrics: pd.DataFrame, out_path: Path) -> None:
    if metrics.empty:
        return
    pivot = metrics.pivot_table(index="sport", columns="feature", values="quintile_lift", aggfunc="first")
    pivot = pivot.reindex(columns=[c for c in _NUMERIC_FEATURES if c in pivot.columns])
    if pivot.empty:
        return
    vmax = float(np.nanmax(np.abs(pivot.values))) if np.isfinite(pivot.values).any() else 0.5
    vmax = max(0.15, min(0.6, vmax * 1.05))

    fig, ax = plt.subplots(figsize=(max(10, pivot.shape[1] * 0.9), max(4, pivot.shape[0] * 0.55)))
    im = ax.imshow(
        np.nan_to_num(pivot.values.astype(float), nan=0.0),
        aspect="auto",
        cmap="YlGn",
        vmin=0.0,
        vmax=vmax,
    )
    ax.set_xticks(range(pivot.shape[1]))
    ax.set_xticklabels(pivot.columns, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(pivot.shape[0]))
    ax.set_yticklabels(pivot.index.astype(str), fontsize=10)
    ax.set_title("Quintile lift: max(bin hit rate) − min(bin hit rate)\nHigher => stronger tiering in that feature")
    plt.colorbar(im, ax=ax, fraction=0.03, pad=0.04, label="lift")
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="black", fontsize=7)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_top_features_by_sport(metrics: pd.DataFrame, out_path: Path, top_k: int = 6) -> None:
    if metrics.empty:
        return
    sports = sorted(metrics["sport"].unique(), key=lambda s: str(s))
    n_sports = len(sports)
    if n_sports == 0:
        return
    ncols = min(3, n_sports)
    nrows = int(np.ceil(n_sports / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 3.4 * nrows), squeeze=False)
    for idx, sp in enumerate(sports):
        r, c = divmod(idx, ncols)
        ax = axes[r][c]
        sub = metrics[metrics["sport"] == sp].copy()
        sub = sub[np.isfinite(sub["point_biserial_r"])]
        sub = sub.sort_values("abs_r", ascending=False).head(top_k)
        if sub.empty:
            ax.set_title(str(sp))
            ax.text(0.5, 0.5, "insufficient data", ha="center", va="center", transform=ax.transAxes)
            continue
        colors = ["#2874A6" if x > 0 else "#C0392B" for x in sub["point_biserial_r"]]
        y_pos = np.arange(len(sub))
        ax.barh(y_pos, sub["point_biserial_r"], color=colors, height=0.7)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(sub["feature"], fontsize=9)
        ax.axvline(0, color="gray", lw=0.8)
        bl = sub["baseline_hit_rate"].iloc[0] if "baseline_hit_rate" in sub.columns else None
        ttl = f"{sp}  (n_decided subset in table)"
        if bl is not None and np.isfinite(bl):
            ttl = f"{sp}  (baseline hit {bl:.1%})"
        ax.set_title(ttl, fontsize=10)
        ax.set_xlabel("point-biserial r")
        ax.invert_yaxis()
    for j in range(len(sports), nrows * ncols):
        r, c = divmod(j, ncols)
        axes[r][c].set_visible(False)
    fig.suptitle("Top features by |correlation| with hit (per sport)", fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_edge_buckets(decided: pd.DataFrame, out_path: Path, min_n_sport: int) -> None:
    """Win rate by edge quintile for main sports with enough volume."""
    if "edge" not in decided.columns:
        return
    wanted = {"nba", "cbb", "nhl", "soccer", "mlb"}
    major = sorted([s for s in decided["_sport"].dropna().unique() if str(s) in wanted], key=str)
    if not major:
        return
    n = len(major)
    fig, axes = plt.subplots(1, n, figsize=(4 * max(n, 1), 4), squeeze=False)
    for idx, sp in enumerate(major):
        ax = axes[0][idx]
        sub = decided[(decided["_sport"] == sp) & decided["is_hit"].notna()].copy()
        sub["edge"] = pd.to_numeric(sub["edge"], errors="coerce")
        sub = sub[np.isfinite(sub["edge"])]
        if len(sub) < min_n_sport:
            ax.set_title(f"{sp}\n(n={len(sub)} < min)")
            continue
        try:
            sub["_q"] = pd.qcut(sub["edge"], q=5, duplicates="drop")
        except ValueError:
            ax.set_title(f"{sp}\nqcut failed")
            continue
        g = sub.groupby("_q", observed=True)["is_hit"].agg(["mean", "count"])
        labels = [f"Q{i + 1}" for i in range(len(g))]
        ax.bar(labels, g["mean"].values, color="#1E8449", alpha=0.85)
        ax.set_ylim(0, min(1.05, max(0.12, float(g["mean"].max()) + 0.1)))
        ax.set_ylabel("hit rate")
        ax.set_title(f"{sp} (n={len(sub)})", fontsize=10)
        for i, (_, row) in enumerate(g.iterrows()):
            ax.text(
                i,
                min(1.0, float(row["mean"]) + 0.02),
                f"n={int(row['count'])}",
                ha="center",
                fontsize=7,
            )
    fig.suptitle("Calibration: hit rate by edge quintile (Q1 lowest edge, Q5 highest)", fontsize=12, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_feature_vs_lift_scatter(metrics: pd.DataFrame, out_path: Path) -> None:
    """|r_pb| vs quintile lift; color=sport; label strong points."""
    if metrics.empty:
        return
    sub = metrics.dropna(subset=["abs_r", "quintile_lift"]).copy()
    sub = sub[np.isfinite(sub["abs_r"]) & np.isfinite(sub["quintile_lift"])]
    if len(sub) < 2:
        return
    fig, ax = plt.subplots(figsize=(9, 6))
    try:
        cmap = matplotlib.colormaps["tab10"]
    except (AttributeError, KeyError):
        cmap = plt.get_cmap("tab10")
    sports = sorted(sub["sport"].astype(str).unique())
    for i, sp in enumerate(sports):
        t = sub[sub["sport"] == sp]
        c = cmap(i % 10)
        ax.scatter(t["abs_r"], t["quintile_lift"], s=48, color=c, label=sp, alpha=0.85, edgecolors="white", linewidths=0.5)
        for _, row in t.iterrows():
            if row["abs_r"] >= 0.14 or row["quintile_lift"] >= 0.18:
                ax.annotate(
                    str(row["feature"]),
                    (float(row["abs_r"]), float(row["quintile_lift"])),
                    fontsize=7,
                    xytext=(4, 4),
                    textcoords="offset points",
                    alpha=0.9,
                )
    ax.set_xlabel("|point-biserial r|")
    ax.set_ylabel("quintile lift (max quintile WR - min quintile WR)")
    ax.set_title("Strength vs tiering: top-right = better raters for stacking portfolios")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="Visualize graded calibration by sport (Box Raw history).")
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=None, help="Output directory for PNG + CSV")
    ap.add_argument("--exclude-demon", action="store_true")
    ap.add_argument("--demon-only", action="store_true")
    ap.add_argument("--min-n-sport", type=int, default=150, help="Minimum decided legs per sport")
    ap.add_argument("--top-k-bars", type=int, default=6)
    args = ap.parse_args()

    root = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else root / "outputs" / "calibration_viz"
    out_dir.mkdir(parents=True, exist_ok=True)

    decided, filt_label = _prepare_frame(root, args.exclude_demon, args.demon_only)
    decided = decided[decided["is_hit"].notna()]
    if len(decided) == 0:
        print("No decided legs after load.")
        return

    metrics = build_metrics(decided, args.min_n_sport)
    if metrics.empty:
        print(f"No per-sport metrics (raise --min-n-sport or check data). n total decided: {len(decided)}")
        return

    csv_path = out_dir / f"sport_feature_metrics_{filt_label}.csv"
    metrics.to_csv(csv_path, index=False)
    print(f"Wrote {csv_path}")

    # Per-sport best feature by |r| (model comparison summary)
    best_rows: list[str] = []
    for sp in sorted(metrics["sport"].unique(), key=str):
        sub = metrics[metrics["sport"] == sp]
        sub = sub[np.isfinite(sub["point_biserial_r"])]
        if sub.empty:
            continue
        i = sub["abs_r"].idxmax()
        row = sub.loc[i]
        best_rows.append(
            f"  {sp}: best_by_|r|={row['feature']} (r_pb={row['point_biserial_r']:.3f}, "
            f"lift={row['quintile_lift']:.3f}, n={int(row['n'])})"
        )
    print("\n--- Best correlates per sport (excluding margin) ---")
    print("\n".join(best_rows) if best_rows else "(none)")

    plot_correlation_heatmap(metrics, out_dir / f"heatmap_point_biserial_{filt_label}.png")
    plot_quintile_lift_heatmap(metrics, out_dir / f"heatmap_quintile_lift_{filt_label}.png")
    plot_top_features_by_sport(metrics, out_dir / f"bars_top_features_{filt_label}.png", top_k=args.top_k_bars)
    plot_edge_buckets(decided, out_dir / f"bars_edge_quintiles_{filt_label}.png", args.min_n_sport)
    plot_feature_vs_lift_scatter(metrics, out_dir / f"scatter_r_vs_lift_{filt_label}.png")

    print(f"\nFigures saved under: {out_dir}")


if __name__ == "__main__":
    main()
