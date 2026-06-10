#!/usr/bin/env python3
"""Stack graded workbooks and summarize hit rates by sport and feature buckets.

Reads `Box Raw` when present; otherwise `GRADED` or the first sheet that contains
a `result` column. Intended for copies under `ui_runner/graded_slate/<date>/` or
`outputs/<date>/`.

Head-to-head columns are rarely persisted on graded sheets; when absent, the
`h2h_bucket` dimension is labeled ``(not in graded export)``.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from utils.graded_schema import normalize_graded_df, recover_direction_if_missing  # noqa: E402
from utils.graded_enrichment import enrich_graded_for_analysis  # noqa: E402


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sport_from_name(name: str) -> str | None:
    n = name.lower()
    if "combined" in n:
        return None
    if "nba1q" in n:
        return "nba1q"
    if "nba1h" in n:
        return "nba1h"
    if "wnba" in n:
        return "wnba"
    if "wcbb" in n:
        return "wcbb"
    if "cbb" in n:
        return "cbb"
    if "nba" in n:
        return "nba"
    if "nhl" in n:
        return "nhl"
    if "soccer" in n:
        return "soccer"
    if "mlb" in n:
        return "mlb"
    if "tennis" in n:
        return "tennis"
    return None


def _pick_sheet(xl: pd.ExcelFile) -> str | None:
    names = xl.sheet_names
    if "Box Raw" in names:
        return "Box Raw"
    if "GRADED" in names:
        return "GRADED"
    lowered = {s.lower(): s for s in names}
    if "graded" in lowered:
        return lowered["graded"]
    for s in names:
        try:
            df = pd.read_excel(xl, sheet_name=s, nrows=5)
        except Exception:
            continue
        cols = {str(c).lower() for c in df.columns}
        if "result" in cols:
            return s
    return names[0] if names else None


def _slate_date_from_path(p: Path) -> str | None:
    for part in p.parts:
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", part)
        if m:
            return m.group(1)
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", p.name)
    return m.group(1) if m else None


def _find_h2h_col(cols: list[str]) -> str | None:
    for c in cols:
        cl = str(c).lower()
        if cl.startswith("h2h") or "h2h_" in cl:
            return c
    return None


def _role_row(r: pd.Series) -> str:
    parts: list[str] = []
    for k in ("shot_role", "usage_role"):
        if k in r.index and pd.notna(r[k]) and str(r[k]).strip():
            parts.append(f"{k.split('_')[0]}:{str(r[k]).strip()}")
    if parts:
        return " | ".join(parts)
    for k in ("position_group", "player_type", "position"):
        if k in r.index and pd.notna(r[k]) and str(r[k]).strip():
            return str(r[k]).strip()
    return "(missing)"


def _graded_stem_base(stem: str) -> str:
    s = stem.lower()
    return s[: -len("_mlbackfill")] if s.endswith("_mlbackfill") else s


def _workbook_priority(p: Path) -> tuple[int, int, str]:
    """Higher tuple sorts later; we pick the last (best) per slate key."""
    name_l = p.name.lower()
    parts_l = {x.lower() for x in p.parts}
    score = 0
    if "_mlbackfill" in name_l:
        score += 100
    if "outputs" in parts_l:
        score += 10
    # Prefer deeper canonical tree over shallow copies
    depth = len(p.parts)
    return (score, depth, str(p.resolve()))


def discover_graded_workbooks(roots: list[Path]) -> list[Path]:
    """All graded workbooks (unique by resolved path)."""
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("graded_*.xlsx")):
            if "combined_tickets_graded" in p.name.lower():
                continue
            if _sport_from_name(p.name) is None:
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def dedupe_graded_workbooks(paths: list[Path]) -> list[Path]:
    """One workbook per logical slate export (drop `_mlbackfill` twins + mirror folders)."""
    best: dict[str, Path] = {}
    for p in paths:
        base = _graded_stem_base(p.stem)
        prev = best.get(base)
        if prev is None or _workbook_priority(p) > _workbook_priority(prev):
            best[base] = p
    return sorted(best.values(), key=lambda x: str(x))


def load_unified(roots: list[Path], *, sport: str | None = None) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    paths = dedupe_graded_workbooks(discover_graded_workbooks(roots))
    sport_f = str(sport or "").strip().lower()
    for p in paths:
        sp = _sport_from_name(p.name)
        if not sp:
            continue
        if sport_f and sp != sport_f:
            continue
        try:
            xl = pd.ExcelFile(p)
        except Exception:
            continue
        sheet = _pick_sheet(xl)
        if not sheet:
            continue
        try:
            df = pd.read_excel(p, sheet_name=sheet)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        df = recover_direction_if_missing(normalize_graded_df(df.copy()))
        df["_sport"] = sp
        df["_source_file"] = p.name
        df["_slate_date"] = _slate_date_from_path(p)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return enrich_graded_for_analysis(out)


def normalize_decided(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    df = raw.copy()
    # result
    if "result" not in df.columns:
        return pd.DataFrame()
    df["result_u"] = df["result"].astype(str).str.strip().str.upper()
    df["is_hit"] = np.where(df["result_u"] == "HIT", 1.0, np.where(df["result_u"] == "MISS", 0.0, np.nan))
    df = df[df["is_hit"].notna()].copy()
    # direction (some exports split `direction` vs `bet_direction`)
    dir_parts: list[pd.Series] = []
    for c in ("bet_direction", "direction", "final_bet_direction"):
        if c not in df.columns:
            continue
        s = df[c].astype(str).str.strip()
        s = s.mask(s.str.lower().isin(["nan", "none", "nat", ""]))
        dir_parts.append(s)
    if dir_parts:
        d0 = dir_parts[0]
        for s in dir_parts[1:]:
            d0 = d0.combine_first(s)
        df["direction"] = d0
    else:
        df["direction"] = np.nan
    df["direction"] = df["direction"].astype(str).str.strip().str.upper()
    df["direction"] = df["direction"].replace({"NAN": "(missing)", "NONE": "(missing)", "": "(missing)"})
    # prop label (coalesce common export variants)
    prop_series: pd.Series | None = None
    for c in ("prop_type_norm", "prop_type", "prop_norm", "Prop", "prop"):
        if c not in df.columns:
            continue
        part = df[c].astype(str).str.strip()
        part = part.mask(part.str.lower().isin(["nan", "none", "nat", ""]))
        if prop_series is None:
            prop_series = part
        else:
            prop_series = prop_series.combine_first(part)
    if prop_series is None:
        df["prop"] = "(missing)"
    else:
        df["prop"] = prop_series.fillna("(missing)")
    # pick_type / market type
    if "pick_type" in df.columns:
        df["pick_type"] = df["pick_type"].astype(str).str.strip()
    else:
        df["pick_type"] = "(missing)"
    # tiers / defense / minutes
    for c in ("minutes_tier", "def_tier", "tier"):
        if c not in df.columns:
            df[c] = np.nan
        df[c] = df[c].astype(str).str.strip()
        df[c] = df[c].replace({"nan": np.nan, "": np.nan})
    df["minutes_tier"] = df["minutes_tier"].fillna("(missing)")
    df["def_tier"] = df["def_tier"].fillna("(missing)")
    df["tier"] = df["tier"].fillna("(missing)")
    # h2h bucket from first matching column if any
    h2h_col = _find_h2h_col([str(c) for c in df.columns])
    if h2h_col:
        df["h2h_bucket"] = df[h2h_col].astype(str).str.strip().replace({"nan": "(missing)", "": "(missing)"})
    else:
        df["h2h_bucket"] = "(not in graded export)"
    # role composite
    df["role"] = df.apply(_role_row, axis=1)
    # ml
    if "ml_prob" in df.columns:
        df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")
    else:
        df["ml_prob"] = np.nan
    for target, alts in (
        ("l5_over", ("l5_over", "last5_over", "L5 Over", "over_L5_raw")),
        ("l5_under", ("l5_under", "last5_under", "L5 Under", "under_L5_raw")),
        ("l10_over", ("l10_over", "L10 Over", "line_hits_over_10", "over_L10", "over_L10_raw")),
        ("l10_under", ("l10_under", "L10 Under", "line_hits_under_10", "under_L10", "under_L10_raw")),
        ("l10_games_played", ("l10_games_played", "line_games_played_10", "Games (10g)", "sample_L10")),
        ("hit_rate", ("hit_rate", "last5_hit_rate", "Hit Rate (5g)", "line_hit_rate_over_ou_5")),
        ("strat_hit_rate", ("strat_hit_rate",)),
        ("strat_n", ("strat_n",)),
        ("hit_rate_l5", ("hit_rate_l5", "Hit Rate L5")),
        ("hit_rate_l10", ("hit_rate_l10", "Hit Rate L10")),
        ("player_hr_historical", ("player_hr_historical", "Player HR Hist")),
        ("opp_hr_historical", ("opp_hr_historical", "Opp HR Hist")),
    ):
        if target not in df.columns:
            df[target] = np.nan
        val = pd.to_numeric(df[target], errors="coerce")
        for alt in alts:
            if alt in df.columns and alt != target:
                val = val.combine_first(pd.to_numeric(df[alt], errors="coerce"))
        df[target] = val
    if "l10_streak" not in df.columns:
        df["l10_streak"] = ""
    else:
        df["l10_streak"] = df["l10_streak"].astype(str).str.strip()
        df.loc[df["l10_streak"].str.lower().isin({"", "nan", "none"}), "l10_streak"] = ""
    return df


def is_demon_pick_type(series: pd.Series) -> pd.Series:
    """True for Demon pick_type rows (case-insensitive)."""
    return series.astype(str).str.strip().str.lower().eq("demon")


def exclude_demons_from_rating(df: pd.DataFrame) -> pd.DataFrame:
    """Drop Demon legs from hit-rate / tier rating (kept in raw graded exports for data collection)."""
    if df.empty or "pick_type" not in df.columns:
        return df
    return df.loc[~is_demon_pick_type(df["pick_type"])].copy()


def exclude_non_rating_legs(df: pd.DataFrame) -> pd.DataFrame:
    """Drop Demon legs and invalid Goblin UNDER rows from hit-rate / tier rating."""
    out = exclude_demons_from_rating(df)
    try:
        from utils.stack_70_eligible import exclude_invalid_market_sides_from_rating

        out = exclude_invalid_market_sides_from_rating(out)
    except ImportError:
        pass
    return out


def agg_dimension(df: pd.DataFrame, sport: str, dim: str, min_n: int) -> pd.DataFrame:
    sub = df[df["_sport"] == sport]
    if sub.empty or dim not in sub.columns:
        return pd.DataFrame()
    g = (
        sub.groupby(dim, dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"), mean_ml=("ml_prob", "mean"))
        .reset_index()
    )
    g["hit_rate"] = g["hits"] / g["n"]
    g = g[g["n"] >= min_n].sort_values(["hit_rate", "n"], ascending=[False, False])
    g.insert(0, "sport", sport)
    g.insert(1, "dimension", dim)
    return g


def top_props(df: pd.DataFrame, sport: str, min_n: int, top_k: int) -> pd.DataFrame:
    sub = df[df["_sport"] == sport]
    if sub.empty:
        return pd.DataFrame()
    g = (
        sub.groupby("prop", dropna=False)
        .agg(n=("is_hit", "size"), hits=("is_hit", "sum"), mean_ml=("ml_prob", "mean"))
        .reset_index()
    )
    g["hit_rate"] = g["hits"] / g["n"]
    g = g[g["n"] >= min_n].sort_values(["hit_rate", "n"], ascending=[False, False]).head(top_k)
    g.insert(0, "sport", sport)
    return g


def run_report(roots: list[Path], min_n: int, top_k: int, out_dir: Path) -> None:
    raw = load_unified(roots)
    decided = normalize_decided(raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    if decided.empty:
        print("No graded rows found under:", ", ".join(str(r) for r in roots))
        return

    demon_n = int(is_demon_pick_type(decided["pick_type"]).sum()) if "pick_type" in decided.columns else 0
    goblin_under_n = 0
    if "pick_type" in decided.columns and "direction" in decided.columns:
        try:
            from utils.stack_70_eligible import is_invalid_market_side

            goblin_under_n = int(
                decided.apply(
                    lambda r: is_invalid_market_side(r["pick_type"], r["direction"]),
                    axis=1,
                ).sum()
            )
        except ImportError:
            goblin_under_n = 0
    decided = exclude_non_rating_legs(decided)
    if decided.empty:
        print("No rated rows after excluding Demon pick types and invalid Goblin UNDER legs.")
        return

    sports = sorted(decided["_sport"].dropna().unique().tolist())
    dims = ["direction", "prop", "minutes_tier", "def_tier", "h2h_bucket", "role", "pick_type", "tier"]

    all_dim_rows: list[pd.DataFrame] = []
    all_top_props: list[pd.DataFrame] = []

    lines: list[str] = []
    lines.append(f"Decided props (HIT/MISS only, Demon + invalid Goblin UNDER excluded): n={len(decided)}")
    if demon_n:
        lines.append(f"  Demon rows excluded from rating: n={demon_n}")
    if goblin_under_n:
        lines.append(f"  Goblin UNDER rows excluded (not a valid market): n={goblin_under_n}")
    lines.append("Counts by sport:")
    for sp in sports:
        n = int((decided["_sport"] == sp).sum())
        lines.append(f"  {sp}: {n}")

    for sp in sports:
        lines.append(f"\n=== {sp.upper()} - top props by hit rate (min_n={min_n}) ===")
        tp = top_props(decided, sp, min_n=min_n, top_k=top_k)
        if tp.empty:
            lines.append("  (not enough volume at this min_n)")
        else:
            for _, r in tp.iterrows():
                lines.append(
                    f"  {r['prop']!s}: hit_rate={r['hit_rate']:.3f} n={int(r['n'])} "
                    f"mean_ml_prob={r['mean_ml']:.3f}" if pd.notna(r["mean_ml"]) else f"  {r['prop']!s}: hit_rate={r['hit_rate']:.3f} n={int(r['n'])}"
                )
        all_top_props.append(tp)
        for dim in dims:
            a = agg_dimension(decided, sp, dim, min_n=min_n)
            if not a.empty:
                all_dim_rows.append(a)

    summary_path = out_dir / "graded_winning_props_by_dimension.csv"
    top_path = out_dir / "graded_top_props_by_sport.csv"
    if all_dim_rows:
        pd.concat(all_dim_rows, ignore_index=True).to_csv(summary_path, index=False)
    if all_top_props:
        pd.concat([x for x in all_top_props if len(x)], ignore_index=True).to_csv(top_path, index=False)

    # prop x direction heat-style table per sport (min_n on cell)
    pivot_path = out_dir / "graded_prop_x_direction_hit_rate.csv"
    pivot_rows: list[pd.DataFrame] = []
    for sp in sports:
        sub = decided[decided["_sport"] == sp]
        if sub.empty:
            continue
        t = (
            sub.groupby(["prop", "direction"])
            .agg(n=("is_hit", "size"), hit_rate=("is_hit", "mean"))
            .reset_index()
        )
        t = t[t["n"] >= min_n]
        if t.empty:
            continue
        t.insert(0, "sport", sp)
        pivot_rows.append(t)
    if pivot_rows:
        pd.concat(pivot_rows, ignore_index=True).to_csv(pivot_path, index=False)

    text_path = out_dir / "graded_winning_props_summary.txt"
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"\nWrote: {summary_path}")
    print(f"Wrote: {top_path}")
    print(f"Wrote: {pivot_path} (if any rows)")
    print(f"Wrote: {text_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=15, help="Minimum sample size per bucket")
    ap.add_argument("--top-k", type=int, default=12, help="Top props to list per sport")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/data/reports/graded_winners)",
    )
    args = ap.parse_args()
    root = _repo_root()
    out_dir = args.out_dir or (root / "data" / "reports" / "graded_winners")
    roots = [root / "ui_runner" / "graded_slate", root / "outputs"]
    run_report(roots, min_n=args.min_n, top_k=args.top_k, out_dir=out_dir)


if __name__ == "__main__":
    main()
