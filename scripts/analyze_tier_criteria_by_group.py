#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = REPO_ROOT / "outputs"

SUPPORTED_SPORTS = ("NBA", "MLB", "NHL", "SOCCER", "WNBA")
LEGACY_NBA_ONLY = ("NBA", "NBA1H", "NBA1Q")

STEP8_CANDIDATES: dict[str, list[Path]] = {
    "NBA": [
        REPO_ROOT / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
    ],
    "NBA1H": [
        REPO_ROOT / "NBA" / "step8_nba1h_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "NBA" / "step8_nba1h_direction_clean.xlsx",
    ],
    "NBA1Q": [
        REPO_ROOT / "NBA" / "step8_nba1q_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "NBA" / "step8_nba1q_direction_clean.xlsx",
    ],
    "MLB": [
        REPO_ROOT / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "MLB" / "outputs" / "step8_mlb_direction_clean.xlsx",
        REPO_ROOT / "MLB" / "step8_mlb_direction_clean.xlsx",
    ],
    "NHL": [
        REPO_ROOT / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "NHL" / "step8_nhl_direction_clean.xlsx",
        REPO_ROOT / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
    ],
    "SOCCER": [
        REPO_ROOT / "Sports" / "Soccer" / "step8_soccer_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
        REPO_ROOT / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
    ],
    "WNBA": [
        REPO_ROOT / "Sports" / "WNBA" / "step8_wnba_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "WNBA" / "step8_wnba_direction.xlsx",
        REPO_ROOT / "WNBA" / "step8_wnba_direction_clean.xlsx",
    ],
}


@dataclass
class GroupSpec:
    pick_type: str
    direction: str
    feature: str
    higher_is_better: bool = True


GROUP_SPECS: list[GroupSpec] = [
    GroupSpec("GOBLIN", "OVER", "tier_distance_score", True),
    GroupSpec("DEMON", "OVER", "tier_distance_score", True),
    GroupSpec("STANDARD", "OVER", "effective_edge", True),
    GroupSpec("STANDARD", "UNDER", "effective_edge", True),
]


def _norm_pick_type(v: Any) -> str:
    s = str(v or "").strip().upper()
    if "GOBLIN" in s:
        return "GOBLIN"
    if "DEMON" in s:
        return "DEMON"
    return "STANDARD"


def _norm_direction(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"UNDER", "LOWER"}:
        return "UNDER"
    return "OVER"


def _norm_prop(v: Any) -> str:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
    except TypeError:
        pass
    if pd.isna(v):
        return ""
    s = str(v).strip().lower()
    if s in ("nan", "none", "nat", ""):
        return ""
    return s


# Column resolution order for stat/prop text (NHL step8 uses prop_type; NBA-style boards use Prop).
_PROP_COLS_GRADED: tuple[str, ...] = (
    "prop_type_norm",
    "prop_type",
    "prop_display",
    "prop display",
    "Prop",
    "stat_norm",
    "prop type",
    "prop",
)
_PROP_COLS_STEP8: tuple[str, ...] = (
    "Prop",
    "prop_type_norm",
    "prop_type",
    "prop_display",
    "prop display",
    "prop_norm",
    "stat_norm",
    "prop",
)


def _result_to_hit(v: Any) -> float | None:
    s = str(v or "").strip().upper()
    if s == "HIT":
        return 1.0
    if s == "MISS":
        return 0.0
    return None


def _pick_col(df: pd.DataFrame, names: list[str]) -> str | None:
    cols = {c.lower(): c for c in df.columns}
    for n in names:
        c = cols.get(n.lower())
        if c is not None:
            return c
    return None


def _load_graded_box_raw(path: Path, sport: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    xls = pd.ExcelFile(path)
    sheet = "Box Raw" if "Box Raw" in xls.sheet_names else xls.sheet_names[0]
    df = pd.read_excel(path, sheet_name=sheet)
    if df.empty:
        return df
    def _col_or_nan(cands: list[str]) -> pd.Series:
        c = _pick_col(df, cands)
        if c is None:
            return pd.Series(np.nan, index=df.index)
        return df[c]
    out = pd.DataFrame(index=df.index)
    out["sport"] = sport
    out["player"] = _col_or_nan(["player", "Player"]).astype(str).str.strip()
    out["prop_type_norm"] = _col_or_nan(list(_PROP_COLS_GRADED)).apply(_norm_prop)
    out["pick_type"] = _col_or_nan(["pick_type", "Pick Type"]).apply(_norm_pick_type)
    out["direction"] = _col_or_nan(["bet_direction", "direction", "Direction"]).apply(_norm_direction)
    out["line"] = pd.to_numeric(_col_or_nan(["line", "Line"]), errors="coerce")
    out["tier"] = _col_or_nan(["tier", "Tier"]).astype(str).str.upper().str.strip()
    out["edge"] = pd.to_numeric(_col_or_nan(["edge", "Edge"]), errors="coerce")
    out["result_hit"] = _col_or_nan(["result", "Result"]).apply(_result_to_hit)
    out = out.dropna(subset=["result_hit", "line"])
    return out


def _load_step8(path: Path, sport: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, low_memory=False)
    else:
        df = pd.read_excel(path)
    if df.empty:
        return df
    def _col_or_nan(cands: list[str]) -> pd.Series:
        c = _pick_col(df, cands)
        if c is None:
            return pd.Series(np.nan, index=df.index)
        return df[c]
    out = pd.DataFrame(index=df.index)
    out["sport"] = sport
    out["player"] = _col_or_nan(["Player", "player"]).astype(str).str.strip()
    out["prop_type_norm"] = _col_or_nan(list(_PROP_COLS_STEP8)).apply(_norm_prop)
    out["pick_type"] = _col_or_nan(["Pick Type", "pick_type"]).apply(_norm_pick_type)
    out["direction"] = _col_or_nan(["Direction", "direction"]).apply(_norm_direction)
    out["line"] = pd.to_numeric(_col_or_nan(["Line", "line"]), errors="coerce")
    out["standard_line"] = pd.to_numeric(_col_or_nan(["Standard Line", "standard_line"]), errors="coerce")
    out["hit_rate"] = pd.to_numeric(_col_or_nan(["Hit Rate (5g)", "hit_rate"]), errors="coerce")
    out["ml_prob"] = pd.to_numeric(_col_or_nan(["ML Prob", "ml_prob"]), errors="coerce")
    return out.dropna(subset=["line"])


def _feature_for_group(sport: str, spec: GroupSpec) -> str:
    """NBA keeps legacy features; other sports evaluate ml_prob directly."""
    s = str(sport or "").strip().upper()
    if s in LEGACY_NBA_ONLY:
        return spec.feature
    return "ml_prob"


def _sport_slug_for_step8(sport_u: str) -> str:
    """Lowercase token expected inside archived step8 filenames (nhl, mlb, soccer, wnba)."""
    s = str(sport_u).strip().upper()
    if s == "SOCCER":
        return "soccer"
    return s.lower()


def _workspace_step8_path(sport: str) -> Path | None:
    sport_u = str(sport).strip().upper()
    for p in STEP8_CANDIDATES.get(sport_u, []):
        if p.exists():
            return p
    return None


def _find_per_date_step8_file(sport: str, date_str: str) -> Path | None:
    """First matching step8 workbook under outputs/<date>/ (and outputs/<date>/archive/)."""
    sport_u = str(sport).strip().upper()
    slug = _sport_slug_for_step8(sport_u)
    root = OUTPUTS_DIR / date_str
    if not root.is_dir():
        return None
    candidates: list[Path] = []
    for sub in (root, root / "archive"):
        if not sub.is_dir():
            continue
        try:
            for p in sub.iterdir():
                if not p.is_file():
                    continue
                name_l = p.name.lower()
                if not name_l.startswith("step8_"):
                    continue
                if slug not in name_l:
                    continue
                if p.suffix.lower() not in (".xlsx", ".xls", ".csv"):
                    continue
                candidates.append(p)
        except OSError:
            continue
    if not candidates:
        return None

    def _sort_key(p: Path) -> tuple[int, int, int, str]:
        # Prefer date-folder root over archive, "clean" in name, xlsx over csv, stable name.
        in_archive = 1 if p.parent.name.lower() == "archive" else 0
        clean = 0 if "clean" in p.name.lower() else 1
        is_xlsx = 0 if p.suffix.lower() == ".xlsx" else 1
        return (in_archive, clean, is_xlsx, p.name.lower())

    candidates.sort(key=_sort_key)
    return candidates[0]


def _resolve_step8_path(
    sport: str,
    date: str | None = None,
    *,
    per_date: bool = False,
) -> Path | None:
    """Resolve step8 path: optional per-date archive under outputs/<date>/, else workspace snapshot."""
    sport_u = str(sport).strip().upper()
    if per_date and date:
        found = _find_per_date_step8_file(sport_u, str(date).strip())
        if found is not None:
            return found
        print(
            f"[analyzer] WARNING: no per-date step8 for {sport_u} {date}, using snapshot",
            flush=True,
        )
    return _workspace_step8_path(sport_u)


def _graded_workbook_candidates(sport: str, date_str: str) -> list[Path]:
    """Candidate graded Box Raw paths under outputs/<date>/ (first match wins)."""
    sport_u = str(sport).strip().upper()
    base = OUTPUTS_DIR / date_str
    if sport_u == "WNBA":
        # Canonical graded_* pattern + WNBA grader output (run_wnba_grader.ps1).
        return [
            base / f"graded_wnba_{date_str}.xlsx",
            base / f"wnba_graded_{date_str}.xlsx",
        ]
    if sport_u == "SOCCER":
        return [base / f"graded_soccer_{date_str}.xlsx"]
    return [base / f"graded_{sport_u.lower()}_{date_str}.xlsx"]


def _first_existing_graded_path(sport: str, date_str: str) -> Path | None:
    for p in _graded_workbook_candidates(sport, date_str):
        if p.exists():
            return p
    return None


def _sports_for_flag(sport_flag: str) -> list[str]:
    s = str(sport_flag).strip().upper()
    if s == "ALL":
        return list(SUPPORTED_SPORTS)
    if s == "NBA":
        # Backward compatibility: old script always analyzed these together.
        return list(LEGACY_NBA_ONLY)
    if s in SUPPORTED_SPORTS:
        return [s]
    raise ValueError(f"Unsupported --sport value: {sport_flag}")


def _find_best_breakpoint(
    grp: pd.DataFrame,
    feature: str,
    *,
    higher_is_better: bool,
    min_n: int,
) -> dict[str, Any]:
    base_n = int(len(grp))
    base_hit = float(grp["result_hit"].mean()) if base_n else float("nan")
    s = pd.to_numeric(grp[feature], errors="coerce")
    valid = grp[s.notna()].copy()
    if valid.empty:
        return {"threshold": None, "n": 0, "hit_rate": np.nan, "lift": np.nan}

    values = pd.to_numeric(valid[feature], errors="coerce")
    qvals = values.quantile([0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]).dropna().unique()
    best: dict[str, Any] | None = None
    for thr in sorted(qvals):
        if higher_is_better:
            cut = valid[values >= thr]
        else:
            cut = valid[values <= thr]
        n = int(len(cut))
        if n < min_n:
            continue
        hr = float(cut["result_hit"].mean())
        cand = {"threshold": float(thr), "n": n, "hit_rate": hr, "lift": hr - base_hit}
        if best is None or cand["lift"] > best["lift"] or (cand["lift"] == best["lift"] and cand["n"] > best["n"]):
            best = cand
    if best is None:
        return {"threshold": None, "n": 0, "hit_rate": np.nan, "lift": np.nan}
    return best


def analyze(date_str: str, min_n: int, sports: list[str]) -> pd.DataFrame:
    graded_frames: list[pd.DataFrame] = []
    step8_frames: list[pd.DataFrame] = []

    for sport in sports:
        graded_path = _first_existing_graded_path(sport, date_str)
        g = _load_graded_box_raw(graded_path, sport) if graded_path is not None else pd.DataFrame()
        if not g.empty:
            graded_frames.append(g)
        step8_path = _resolve_step8_path(sport)
        if step8_path is not None:
            s = _load_step8(step8_path, sport)
            if not s.empty:
                step8_frames.append(s)

    if not graded_frames:
        raise FileNotFoundError(f"No graded Box Raw files found for date {date_str}")
    graded = pd.concat(graded_frames, ignore_index=True)
    step8 = pd.concat(step8_frames, ignore_index=True) if step8_frames else pd.DataFrame()

    if not step8.empty:
        merge_keys = ["sport", "player", "prop_type_norm", "pick_type", "direction", "line"]
        step8_dedup = step8.sort_values(["sport", "player"]).drop_duplicates(subset=merge_keys, keep="first")
        cols_keep = merge_keys + [c for c in ("standard_line", "hit_rate", "ml_prob") if c in step8_dedup.columns]
        df = graded.merge(step8_dedup[cols_keep], on=merge_keys, how="left")
    else:
        df = graded.copy()
        df["standard_line"] = np.nan
        df["hit_rate"] = np.nan
        df["ml_prob"] = np.nan

    df["goblin_distance"] = (pd.to_numeric(df["line"], errors="coerce") - pd.to_numeric(df["standard_line"], errors="coerce")).abs()
    distance_source = "line_vs_standard"
    if pd.to_numeric(df["goblin_distance"], errors="coerce").notna().sum() == 0:
        # Historical graded files do not always carry Standard Line snapshots.
        # Fall back to abs(edge) so group-wise tier analysis still runs.
        df["goblin_distance"] = pd.to_numeric(df["edge"], errors="coerce").abs()
        distance_source = "abs_edge_proxy"
    df["tier_distance_score"] = df.apply(
        lambda r: (
            r["goblin_distance"] if r["pick_type"] == "GOBLIN"
            else (-r["goblin_distance"] if r["pick_type"] == "DEMON" else 0.0)
        ),
        axis=1,
    )

    df["effective_edge"] = df.apply(
        lambda r: (-float(r["edge"]) if r["direction"] == "UNDER" else float(r["edge"])) if pd.notna(r["edge"]) else np.nan,
        axis=1,
    )
    hr01 = pd.to_numeric(df["hit_rate"], errors="coerce")
    hr01 = np.where((hr01 > 1.0) & (hr01 <= 100.0), hr01 / 100.0, hr01)
    df["hit_rate_01"] = hr01
    df["effective_hit_rate"] = np.where(df["direction"].eq("UNDER"), 1.0 - df["hit_rate_01"], df["hit_rate_01"])

    rows: list[dict[str, Any]] = []
    for sport in sorted(set(df["sport"].astype(str))):
        sdf = df[df["sport"].astype(str) == sport].copy()
        for spec in GROUP_SPECS:
            grp = sdf[(sdf["pick_type"] == spec.pick_type) & (sdf["direction"] == spec.direction)].copy()
            feat = _feature_for_group(sport, spec)
            n = int(len(grp))
            if n == 0:
                rows.append(
                    {
                        "date": date_str,
                        "sport": sport,
                        "group": f"{spec.pick_type} {spec.direction}",
                        "feature": feat,
                        "n": 0,
                        "base_hit_rate": np.nan,
                        "corr_feature_vs_hit": np.nan,
                        "best_threshold": np.nan,
                        "best_n": 0,
                        "best_hit_rate": np.nan,
                        "lift_vs_base": np.nan,
                    }
                )
                continue
            base_hr = float(grp["result_hit"].mean())
            feature_vals = pd.to_numeric(grp[feat], errors="coerce")
            corr = float(feature_vals.corr(grp["result_hit"])) if feature_vals.notna().sum() >= 3 else np.nan
            best = _find_best_breakpoint(grp, feat, higher_is_better=spec.higher_is_better, min_n=min_n)
            rows.append(
                {
                    "date": date_str,
                    "sport": sport,
                    "group": f"{spec.pick_type} {spec.direction}",
                    "feature": feat,
                    "n": n,
                    "base_hit_rate": base_hr,
                    "corr_feature_vs_hit": corr,
                    "best_threshold": best["threshold"],
                    "best_n": best["n"],
                    "best_hit_rate": best["hit_rate"],
                    "lift_vs_base": best["lift"],
                    "distance_source": distance_source,
                }
            )
    return pd.DataFrame(rows)


def ml_prob_threshold_scan(
    sport: str,
    dates: list[str],
    thresholds: list[float],
    *,
    step8_per_date: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge graded history with step8 ml_prob; scan hit rates above each threshold (>=).

    Rows are emitted for pick_type in GOBLIN, DEMON, STANDARD, and ALL (pooled).

    With ``step8_per_date=False`` (default), uses a single workspace step8 snapshot — same as
    historical behavior. With ``step8_per_date=True``, uses ``outputs/<date>/step8_*`` when
    present so each graded date merges against that slate's board.
    """
    sport_u = str(sport).strip().upper()
    frames: list[pd.DataFrame] = []
    dates_with_graded = 0
    per_date_step8_hits = 0
    for d in dates:
        gp = _first_existing_graded_path(sport_u, d)
        if gp is None:
            continue
        g = _load_graded_box_raw(gp, sport_u)
        if g.empty:
            continue
        dates_with_graded += 1
        if step8_per_date:
            s_path = _find_per_date_step8_file(sport_u, d)
            if s_path is not None:
                per_date_step8_hits += 1
            else:
                print(
                    f"[analyzer] WARNING: no per-date step8 for {sport_u} {d}, using snapshot",
                    flush=True,
                )
                s_path = _workspace_step8_path(sport_u)
        else:
            s_path = _workspace_step8_path(sport_u)
        if s_path is None:
            continue
        s = _load_step8(s_path, sport_u)
        if s.empty:
            continue
        merge_keys = ["sport", "player", "prop_type_norm", "pick_type", "direction", "line"]
        s = s.sort_values(["sport", "player"]).drop_duplicates(subset=merge_keys, keep="first")
        m = g.merge(s[merge_keys + ["ml_prob"]], on=merge_keys, how="left")
        m = m[pd.to_numeric(m["ml_prob"], errors="coerce").notna()].copy()
        if not m.empty:
            frames.append(m)
    meta: dict[str, Any] = {
        "step8_per_date": bool(step8_per_date),
        "dates_with_graded": int(dates_with_graded),
        "per_date_step8_hits": int(per_date_step8_hits),
    }
    if not frames:
        return [], meta
    all_df = pd.concat(frames, ignore_index=True)
    all_df["ml_prob"] = pd.to_numeric(all_df["ml_prob"], errors="coerce")
    base_hit = float(all_df["result_hit"].mean()) if len(all_df) else float("nan")
    out: list[dict[str, Any]] = []
    for pick_type in ("GOBLIN", "DEMON", "STANDARD", "ALL"):
        sub = all_df if pick_type == "ALL" else all_df[all_df["pick_type"] == pick_type]
        if sub.empty:
            continue
        base_pt = float(sub["result_hit"].mean())
        for thr in thresholds:
            cut = sub[sub["ml_prob"] >= thr]
            n_above = int(len(cut))
            hit_above = float(cut["result_hit"].mean()) if n_above else float("nan")
            out.append(
                {
                    "pick_type": pick_type,
                    "threshold": float(thr),
                    "n_total": int(len(sub)),
                    "base_hit_rate": base_pt,
                    "n_above": n_above,
                    "hit_rate_above": hit_above,
                    "lift_vs_base": (hit_above - base_pt) if n_above else float("nan"),
                    "lift_vs_global_base": (hit_above - base_hit) if n_above else float("nan"),
                }
            )
    return out, meta


def _print_ml_prob_threshold_scan(
    scan_df: pd.DataFrame,
    *,
    sport_label: str,
    by_pick_type: bool,
) -> None:
    cols = ["threshold", "n_total", "base_hit_rate", "n_above", "hit_rate_above", "lift_vs_base"]
    if by_pick_type:
        order = ("GOBLIN", "DEMON", "STANDARD", "ALL")
        for pt in order:
            sub = scan_df[scan_df["pick_type"].eq(pt)]
            if sub.empty:
                continue
            print(f"\n{sport_label} ml_prob threshold scan — {pt} (n_total={int(sub['n_total'].iloc[0])}):")
            print(sub[cols].to_string(index=False))
    else:
        sub = scan_df[scan_df["pick_type"].eq("ALL")]
        if sub.empty:
            return
        print(f"\n{sport_label} ml_prob threshold scan (ALL pick types):")
        print(sub[["threshold", "n_above", "hit_rate_above", "lift_vs_base"]].to_string(index=False))


def _parse_date_token(s: str) -> pd.Timestamp:
    return pd.to_datetime(str(s).strip(), format="%Y-%m-%d", errors="raise")


def _discover_dates(from_date: str | None, to_date: str | None) -> list[str]:
    lo = _parse_date_token(from_date) if from_date else None
    hi = _parse_date_token(to_date) if to_date else None
    out: list[str] = []
    if not OUTPUTS_DIR.exists():
        return out
    for p in OUTPUTS_DIR.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        try:
            d = _parse_date_token(name)
        except Exception:
            continue
        if lo is not None and d < lo:
            continue
        if hi is not None and d > hi:
            continue
        out.append(name)
    out.sort()
    return out


def _aggregate_across_dates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rows: list[dict[str, Any]] = []
    for (sport, group), grp in df.groupby(["sport", "group"], dropna=False):
        n_total = int(pd.to_numeric(grp["n"], errors="coerce").fillna(0).sum())
        if n_total <= 0:
            continue
        base_weighted = float(
            (
                pd.to_numeric(grp["base_hit_rate"], errors="coerce").fillna(0.0)
                * pd.to_numeric(grp["n"], errors="coerce").fillna(0.0)
            ).sum()
            / n_total
        )
        best_n_total = int(pd.to_numeric(grp["best_n"], errors="coerce").fillna(0).sum())
        best_weighted = (
            float(
                (
                    pd.to_numeric(grp["best_hit_rate"], errors="coerce").fillna(0.0)
                    * pd.to_numeric(grp["best_n"], errors="coerce").fillna(0.0)
                ).sum()
                / best_n_total
            )
            if best_n_total > 0
            else float("nan")
        )
        corr_vals = pd.to_numeric(grp["corr_feature_vs_hit"], errors="coerce")
        corr_med = float(corr_vals.median()) if corr_vals.notna().any() else float("nan")
        thr_vals = pd.to_numeric(grp["best_threshold"], errors="coerce")
        thr_med = float(thr_vals.median()) if thr_vals.notna().any() else float("nan")
        rows.append(
            {
                "group": group,
                "sport": sport,
                "feature": str(grp["feature"].iloc[0]),
                "dates": int(grp["date"].nunique()),
                "n_total": n_total,
                "base_hit_rate_weighted": base_weighted,
                "best_threshold_median": thr_med,
                "best_n_total": best_n_total,
                "best_hit_rate_weighted": best_weighted,
                "lift_vs_base_weighted": (best_weighted - base_weighted) if pd.notna(best_weighted) else float("nan"),
                "corr_feature_vs_hit_median": corr_med,
            }
        )
    return pd.DataFrame(rows).sort_values(["sport", "group"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze tier criteria by pick-type + direction group.")
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"), help="Date in YYYY-MM-DD")
    ap.add_argument("--sport", default="NBA", choices=["NBA", "MLB", "NHL", "Soccer", "WNBA", "ALL"])
    ap.add_argument("--all-dates", action="store_true", help="Analyze all dates under outputs/YYYY-MM-DD")
    ap.add_argument("--from", dest="from_date", default="", help="Start date inclusive (YYYY-MM-DD)")
    ap.add_argument("--to", dest="to_date", default="", help="End date inclusive (YYYY-MM-DD)")
    ap.add_argument("--group", default="", help='Optional group filter, e.g. "STANDARD UNDER"')
    ap.add_argument("--by-date", action="store_true", help="Print/report per-date rows (optionally filtered by --group)")
    ap.add_argument("--min-n-per-day", type=int, default=0, help="Optional minimum per-date group sample size (n)")
    ap.add_argument("--min-n", type=int, default=25, help="Minimum sample size for breakpoint candidate")
    ap.add_argument("--output", default="", help="Optional output JSON path")
    ap.add_argument(
        "--threshold-scan-pick-types",
        action="store_true",
        help="With ml_prob threshold scan sports, print GOBLIN / DEMON / STANDARD / ALL tables (not pooled-only).",
    )
    ap.add_argument(
        "--step8-per-date",
        action="store_true",
        help="For each graded date, prefer outputs/<date>/step8_*.{xlsx,csv} over the workspace snapshot.",
    )
    args = ap.parse_args()
    sports = _sports_for_flag(args.sport)

    if args.all_dates or args.from_date or args.to_date:
        dates = _discover_dates(args.from_date or None, args.to_date or None)
    else:
        dates = [str(args.date)]

    all_rows: list[pd.DataFrame] = []
    for d in dates:
        try:
            r = analyze(d, args.min_n, sports)
            if not r.empty:
                all_rows.append(r)
        except FileNotFoundError:
            continue

    report = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    if args.group and not report.empty:
        group_norm = str(args.group).strip().upper()
        report = report[report["group"].astype(str).str.upper() == group_norm].copy()
    if args.min_n_per_day and not report.empty:
        n_s = pd.to_numeric(report["n"], errors="coerce").fillna(0)
        report = report[n_s >= int(args.min_n_per_day)].copy()
    agg = _aggregate_across_dates(report) if not report.empty else pd.DataFrame()

    pd.options.display.width = 240
    pd.options.display.max_columns = 24
    if args.by_date:
        print(
            f"Tier criteria by-date rows={len(report)} sport={args.sport} "
            f"group={args.group or 'ALL'} min_n={args.min_n}"
        )
        if not report.empty:
            by_date = report.sort_values(["date", "sport", "group"]).reset_index(drop=True)
            print(by_date.to_string(index=False))
        else:
            print("No matching rows found for requested by-date view.")
    elif args.all_dates or args.from_date or args.to_date:
        print(
            f"Tier criteria analysis sport={args.sport} dates={len(dates)} considered, "
            f"rows={len(report)} min_n={args.min_n}"
        )
        if not agg.empty:
            print(agg.to_string(index=False))
        else:
            print("No analyzable rows found in selected date range.")
    else:
        print(f"Tier criteria analysis sport={args.sport} date={args.date} min_n={args.min_n}")
        print(report.to_string(index=False))

    scan_rows: list[dict[str, Any]] = []
    scan_meta: dict[str, Any] = {}
    sport_scan = str(args.sport).strip().upper()
    if sport_scan in {"SOCCER", "MLB", "NHL", "WNBA"}:
        if sport_scan == "SOCCER":
            scan_thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]
        else:
            scan_thresholds = [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65, 0.68, 0.71, 0.74]
        scan_rows, scan_meta = ml_prob_threshold_scan(
            sport_scan,
            dates,
            scan_thresholds,
            step8_per_date=bool(args.step8_per_date),
        )
        if args.step8_per_date:
            n_hit = int(scan_meta.get("per_date_step8_hits", 0))
            m_dates = int(scan_meta.get("dates_with_graded", 0))
            print(
                f"[analyzer] step8 source: per-date ({n_hit} matched / {m_dates} dates with graded)",
                flush=True,
            )
        else:
            print("[analyzer] step8 source: snapshot only (--step8-per-date not set)", flush=True)
        if scan_rows:
            _print_ml_prob_threshold_scan(
                pd.DataFrame(scan_rows),
                sport_label=sport_scan,
                by_pick_type=bool(args.threshold_scan_pick_types),
            )

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "params": {
                "date": args.date,
                "sport": args.sport,
                "all_dates": bool(args.all_dates),
                "from": args.from_date or None,
                "to": args.to_date or None,
                "min_n": int(args.min_n),
                "dates_considered": dates,
                "group": args.group or None,
                "by_date": bool(args.by_date),
                "min_n_per_day": int(args.min_n_per_day),
                "threshold_scan_pick_types": bool(args.threshold_scan_pick_types),
                "step8_per_date": bool(args.step8_per_date),
            },
            "aggregate": agg.to_dict(orient="records") if not agg.empty else [],
            "per_date_rows": report.to_dict(orient="records") if not report.empty else [],
            "ml_prob_threshold_scan_meta": scan_meta,
            "ml_prob_threshold_scan": scan_rows,
            "soccer_threshold_scan": scan_rows if str(args.sport).strip().upper() == "SOCCER" else [],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()

