#!/usr/bin/env python3
"""
Build training CSVs from ui_runner/templates/graded_props_*.json joined to step8 slates.

Outputs:
  data/retrain_dataset.csv              — decided props + step8 features (left join)
  data/retrain_dataset_graded_only.csv  — decided props from JSON only (baseline)

  With ``--output PATH``, writes the joined CSV to PATH and graded-only to
  ``<stem>_graded_only<suffix>`` beside it. Use ``--from YYYY-MM-DD`` to keep only
  graded_props rows whose file_date is on/after that day (e.g. post tier overhaul).

Usage:
  py -3.14 scripts/build_retrain_dataset.py
  py -3.14 scripts/build_retrain_dataset.py --repo-root .
  py -3.14 scripts/build_retrain_dataset.py --from 2026-05-02 --output data/training/retrain_post_tier.csv
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from grading.slate_grader import norm_player_key, norm_prop_key  # noqa: E402
from utils.graded_schema import coverage_report, normalize_graded_df, recover_direction_if_missing  # noqa: E402

# Per-group tier overhaul (commit a1b24e77). Graded `file_date` on/after this uses new tier semantics.
TIER_OVERHAUL_DATE = "2026-05-02"

STEP8_FEATURE_COLS = [
    "blended_score",
    "edge_score",
    "def_tier",
    "rank_score",
    "ml_edge",
    "deviation_level",
    "pp_projection_id",
]

# Raw step4b/c/d enrichment columns (joined from step8 when pipeline has run them).
ENRICHMENT_RAW_COLS = [
    # MLB
    "batting_order_pos",
    "top_of_order",
    "opp_pitcher_era_vs_batter_hand",
    "opp_pitcher_k9_vs_batter_hand",
    "pitcher_advantage",
    "park_factor_overall",
    "park_tier",
    "wind_speed_mph",
    "wind_out_to_cf",
    "weather_flag",
    "role_stability_score",
    # NHL
    "pp_toi_per_game",
    "pp_toi_pct",
    "pp_unit_tier",
    "line_combo_toi_pct",
    "line_combo_cf_pct",
    "line_combo_xgf_pct",
    "on_pp1_line",
    # WNBA
    "star_tier",
    "is_franchise_star",
    "foul_trouble_risk",
    "b2b_flag",
    "b2b_rest_context",
    # NBA / WNBA (usage + pace from step4b)
    "usage_pct",
    "usage_tier",
    "team_pace",
    "opp_pace",
    "pace_delta",
    "pace_context",
    # NBA
    "reb_pct",
    "ast_pct",
    "game_pace",
    "usage_role_type",
    "opp_def_rating",
    "team_implied_total",
    "opp_implied_total",
    "game_script_context",
    "team_star_out",
    "key_facilitator_out",
    "usage_vacuum",
    "injury_boost_candidate",
    "high_variance_role",
    "minutes_floor_L10",
    "minutes_ceil_L10",
    "minutes_cv_L10",
    "opp_pts_allowed_vs_position",
    "opp_reb_allowed_vs_position",
    "opp_ast_allowed_vs_position",
    "positional_matchup_tier",
    # Soccer
    "player_xg_per90",
    "player_xag_per90",
    "player_goals_minus_xg",
    "player_shots_per90",
    "xg_tier",
    "xg_data_source",
    # L10 streak (all sports with game logs)
    "l10_over",
    "l10_under",
    "l10_over_pct",
    "l10_streak",
]

JOIN_FEATURE_COLS = list(dict.fromkeys(STEP8_FEATURE_COLS + ENRICHMENT_RAW_COLS))


def normalize_name(s: Any) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.casefold().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\.?$", "", s).strip()
    return s


def normalize_prop(s: Any) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.casefold().strip()
    s = re.sub(r"[\s_]+", "", s)
    return s


def normalize_line(v: Any) -> str:
    try:
        return str(round(float(str(v).replace(",", "")), 4))
    except (TypeError, ValueError):
        return str(v).strip()


def normalize_pick_type(s: Any) -> str:
    """Graded JSON often uses em dash for Standard pick type; step8 uses ``standard``."""
    raw = str(s or "").strip()
    if raw in ("", "—", "–", "-", "NaN", "nan", "None", "none"):
        return "standard"
    rf = raw.casefold()
    if rf in ("std",):
        return "standard"
    if rf in ("goblin", "demon", "standard"):
        return rf
    return rf


def normalize_pick_type_group(pick_type: Any, direction: Any) -> str:
    pt = normalize_pick_type(pick_type)
    d = str(direction or "").strip().upper()
    if pt == "standard":
        if d == "OVER":
            return "Standard OVER"
        if d == "UNDER":
            return "Standard UNDER"
        return "Standard (unknown dir)"
    if pt == "goblin":
        return "Goblin"
    if pt == "demon":
        return "Demon"
    return "(missing)"


def normalize_def_tier(s: Any) -> str:
    t = str(s or "").strip().casefold()
    if not t or t in {"nan", "none", "null", "(missing)"}:
        return "(missing)"
    if t in {"elite", "el", "top", "strongest"}:
        return "Elite"
    if t in {"above avg", "above average", "above_avg", "good", "solid", "plus"}:
        return "Above Avg"
    if t in {"avg", "average", "neutral", "mid", "medium"}:
        return "Avg"
    if t in {"below avg", "below average", "below_avg", "poor"}:
        return "Below Avg"
    if t in {"weak", "bottom", "bad"}:
        return "Weak"
    return "(missing)"


def normalize_direction(s: Any) -> str:
    return str(s or "").strip().upper()


def _repo_root(arg: Path | None) -> Path:
    return Path(arg).resolve() if arg else Path(__file__).resolve().parent.parent


def _first_column_series(df: pd.DataFrame, name: str) -> pd.Series | None:
    """Return a single Series when Excel/CSV has duplicate header names."""
    if name not in df.columns:
        return None
    col = df[name]
    if isinstance(col, pd.DataFrame):
        col = col.iloc[:, 0]
    return col


def _game_date_series(df: pd.DataFrame, anchor_file_date: str | None = None) -> pd.Series:
    """Normalize calendar day for step8 rows.

    Soccer (and some exports) only have ``Game Time`` like ``05/02 7:30 PM`` with no year;
    pandas may parse that as year 0001. When ``anchor_file_date`` is ``YYYY-MM-DD``, remap
    those implausible years to the anchor year so the ±1d slate filter matches ``file_date``.
    """
    ts = None
    for col_name in ("game_date", "start_time", "game_start", "Game Date", "Game Time"):
        ser = _first_column_series(df, col_name)
        if ser is not None:
            ts = pd.to_datetime(ser, errors="coerce")
            break
    if ts is None:
        return pd.Series(pd.NaT, index=df.index)
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    ts = ts.dt.normalize()
    d = str(anchor_file_date or "").strip()[:10]
    if len(d) == 10:
        anchor = pd.to_datetime(d, errors="coerce")
        if pd.notna(anchor):
            y = int(anchor.year)
            bad = ts.notna() & (ts.dt.year < 1900)
            if bad.any():
                sub = ts.loc[bad]
                ts = ts.copy()
                ts.loc[bad] = pd.to_datetime(
                    {
                        "year": np.repeat(y, int(bad.sum())),
                        "month": sub.dt.month.to_numpy(),
                        "day": sub.dt.day.to_numpy(),
                    },
                    errors="coerce",
                ).dt.normalize()
    return ts


def _step8_prop_series(df: pd.DataFrame) -> pd.Series:
    for c in ("prop_norm", "prop_type_norm", "prop_type", "Prop Type", "Prop"):
        if c in df.columns:
            return df[c]
    return pd.Series([""] * len(df), index=df.index)


def _step8_direction_series(df: pd.DataFrame) -> pd.Series:
    """NHL / many CSV exports use lowercase ``direction`` (not ``Direction``)."""
    lower = {str(c).lower(): c for c in df.columns}
    for key in ("final_bet_direction", "bet_direction", "direction"):
        if key in lower:
            return df[lower[key]]
    if "Direction" in df.columns:
        return df["Direction"]
    return pd.Series([""] * len(df), index=df.index)


def _canonicalize_step8_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Align dated step8 Excel headers with pipeline CSV names."""
    ren = {
        "Rank Score": "rank_score",
        "Blended Score": "blended_score",
        "Edge Score": "edge_score",
        "ML Edge": "ml_edge",
        "ML Prob": "ml_prob",
        "Deviation Level": "deviation_level",
        "Deviation": "deviation_level",
        "Dev Level": "deviation_level",
        "Def Tier": "def_tier",
        "Pick Type": "pick_type",
        "Player": "player",
        "Line": "line",
        "Game Date": "game_date",
        "Opp": "opp_team",
        "Team": "team",
    }
    out = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    out = out.loc[:, ~out.columns.duplicated()]
    return out


def _pick_col(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    lower = {str(c).lower(): c for c in df.columns}
    for n in names:
        if n in df.columns:
            return df[n]
        nl = n.lower()
        if nl in lower:
            return df[lower[nl]]
    return pd.Series([np.nan] * len(df), index=df.index)


def load_step8_sport(root: Path, sport: str) -> pd.DataFrame | None:
    """Load canonical step8 table for a sport (single snapshot file on disk)."""
    sport_u = sport.upper()
    if sport_u == "NBA":
        p = root / "NBA" / "data" / "outputs" / "step8_all_direction.csv"
        if not p.is_file():
            return None
        return pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
    if sport_u == "MLB":
        for p in (
            root / "Sports" / "MLB" / "data" / "outputs" / "step8_mlb_direction_clean.xlsx",
            root / "Sports" / "MLB" / "data" / "outputs" / "step8_mlb_direction.csv",
            root / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
            root / "Sports" / "MLB" / "step8_mlb_direction.csv",
        ):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".xlsx":
                return pd.read_excel(p, engine="openpyxl")
            return pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
        return None
    if sport_u == "NHL":
        for p in (
            root / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
            root / "Sports" / "NHL" / "step8_nhl_direction_clean.csv",
            root / "Sports" / "NHL" / "data" / "outputs" / "step8_nhl_direction.csv",
            root / "NHL" / "data" / "outputs" / "step8_nhl_direction.csv",
            root / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
            root / "NHL" / "outputs" / "step8_nhl_direction.csv",
        ):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".xlsx":
                return pd.read_excel(p, engine="openpyxl")
            return pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
        return None
    if sport_u == "WNBA":
        for p in (
            root / "Sports" / "WNBA" / "step8_wnba_direction_clean.xlsx",
            root / "Sports" / "WNBA" / "data" / "outputs" / "step8_wnba_direction_clean.xlsx",
        ):
            if p.is_file():
                return pd.read_excel(p, engine="openpyxl")
        return None
    if sport_u == "SOCCER" or sport == "Soccer":
        for p in (
            root / "Sports" / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
            root / "Sports" / "Soccer" / "step8_soccer_direction.csv",
            root / "Sports" / "Soccer" / "outputs" / "step8_soccer_direction.csv",
            root / "Soccer" / "step8_soccer_direction.csv",
            root / "Soccer" / "outputs" / "step8_soccer_direction.csv",
        ):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".xlsx":
                return pd.read_excel(p, engine="openpyxl")
            df = pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
            if len(df) > 0:
                return df
        return None
    return None


def load_step8_dated_snapshot(root: Path, sport: str, file_date: str) -> pd.DataFrame | None:
    """Prefer outputs/<date>/step8_* for historical slates; else fall back to repo snapshot."""
    d = (file_date or "")[:10]
    if len(d) != 10:
        return None
    sport_u = sport.upper()
    if sport_u == "NBA":
        # Prefer dated pipeline step8 (has game_date / start_time). Avoid
        # outputs/<d>/step8_all_direction_clean.xlsx when it is a Grades/UI export
        # without calendar columns (would make date filter drop all rows).
        for name in (f"step8_nba_direction_clean_{d}.xlsx", f"step8_all_direction_{d}.xlsx"):
            p = root / "outputs" / d / name
            if p.is_file():
                return (
                    pd.read_excel(p, engine="openpyxl")
                    if p.suffix.lower() == ".xlsx"
                    else pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
                )
        return load_step8_sport(root, sport)
    if sport_u == "MLB":
        for name in (f"step8_mlb_direction_clean_{d}.xlsx", f"step8_mlb_direction_{d}.xlsx"):
            p = root / "outputs" / d / name
            if p.is_file():
                return (
                    pd.read_excel(p, engine="openpyxl")
                    if p.suffix.lower() == ".xlsx"
                    else pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
                )
        return load_step8_sport(root, sport)
    if sport_u == "NHL":
        for name in (f"step8_nhl_direction_clean_{d}.xlsx", f"step8_nhl_direction_{d}.xlsx"):
            p = root / "outputs" / d / name
            if p.is_file():
                return (
                    pd.read_excel(p, engine="openpyxl")
                    if p.suffix.lower() == ".xlsx"
                    else pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
                )
        return load_step8_sport(root, sport)
    if sport_u == "SOCCER" or sport == "Soccer":
        for name in (f"step8_soccer_direction_clean_{d}.xlsx", f"step8_soccer_direction_{d}.xlsx"):
            p = root / "outputs" / d / name
            if p.is_file():
                return (
                    pd.read_excel(p, engine="openpyxl")
                    if p.suffix.lower() == ".xlsx"
                    else pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
                )
        return load_step8_sport(root, "Soccer")
    if sport_u == "WNBA":
        for name in (f"step8_wnba_direction_clean_{d}.xlsx",):
            p = root / "outputs" / d / name
            if p.is_file():
                return pd.read_excel(p, engine="openpyxl")
        return load_step8_sport(root, sport)
    return None


def prop_join_key(s: Any) -> str:
    """Align graded JSON `prop` text with step8 prop_norm / prop_type_norm."""
    return normalize_prop(norm_prop_key(s))


def player_join_key(s: Any) -> str:
    """Match slate_grader / backfill_graded_ml_columns player keys."""
    return str(norm_player_key(s) or "").casefold().strip()


def _prepare_step8(df: pd.DataFrame, anchor_file_date: str | None = None) -> pd.DataFrame:
    out = _canonicalize_step8_columns(df.copy())
    out["_n_player"] = out["player"].map(player_join_key) if "player" in out.columns else ""
    out["_n_prop"] = _step8_prop_series(out).map(prop_join_key)
    out["_n_line"] = out["line"].map(normalize_line) if "line" in out.columns else ""
    pt = _pick_col(out, ("pick_type", "Pick Type"))
    out["_n_pick"] = pt.map(normalize_pick_type)
    out["_n_dir"] = _step8_direction_series(out).map(normalize_direction)
    out["_game_d"] = _game_date_series(out, anchor_file_date=anchor_file_date)
    return out


def load_all_graded_props(templates_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(templates_dir.glob("graded_props_*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        file_date = str(raw.get("date") or "")[:10]
        for p in raw.get("props") or []:
            if not isinstance(p, dict):
                continue
            r = dict(p)
            r["file_date"] = file_date
            rows.append(r)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _pick_graded_sheet(xl: pd.ExcelFile) -> str | None:
    names = list(xl.sheet_names)
    if not names:
        return None
    if "Box Raw" in names:
        return "Box Raw"
    if "GRADED" in names:
        return "GRADED"
    for s in names:
        try:
            probe = pd.read_excel(xl, sheet_name=s, nrows=5)
        except Exception:
            continue
        cols = {str(c).lower() for c in probe.columns}
        if "result" in cols or "actual_status" in cols:
            return s
    return names[0]


def _load_graded_workbook_rows(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (before_df, after_df) for graded workbook schema coverage.
    before_df only includes canonical columns if already present.
    after_df applies alias normalization and direction recovery.
    """
    paths = sorted((root / "outputs").glob("*/graded_*.xlsx"))
    before_frames: list[pd.DataFrame] = []
    after_frames: list[pd.DataFrame] = []
    canonical_fields = ["direction", "def_tier", "minutes_tier", "pick_type", "tier", "result"]

    for p in paths:
        try:
            xl = pd.ExcelFile(p)
            sheet = _pick_graded_sheet(xl)
            if not sheet:
                continue
            df = pd.read_excel(p, sheet_name=sheet)
        except Exception:
            continue
        if df is None or df.empty:
            continue

        raw = df.copy()
        raw["source_file"] = p.name
        for f in canonical_fields:
            if f not in raw.columns:
                raw[f] = np.nan
        before_frames.append(raw[canonical_fields + ["source_file"]].copy())

        norm = normalize_graded_df(df.copy())
        norm = recover_direction_if_missing(norm)
        if "result" in norm.columns:
            ru = norm["result"].astype(str).str.strip().str.upper()
            norm = norm.loc[ru.isin(("HIT", "MISS")) | ru.isin(("WIN", "LOSS", "WON", "LOSE"))].copy()
        norm["source_file"] = p.name
        for f in canonical_fields:
            if f not in norm.columns:
                norm[f] = np.nan
        after_frames.append(norm[canonical_fields + ["source_file", "direction_source"]].copy())

    before_df = pd.concat(before_frames, ignore_index=True) if before_frames else pd.DataFrame(columns=canonical_fields)
    after_df = pd.concat(after_frames, ignore_index=True) if after_frames else pd.DataFrame(columns=canonical_fields)
    return before_df, after_df


def _print_field_coverage(prefix: str, report: dict[str, dict[str, Any]]) -> None:
    print(prefix)
    for f in ("direction", "def_tier", "minutes_tier", "pick_type", "tier", "result"):
        r = report.get(f, {"known": 0, "total": 0, "pct": 0.0})
        print(f"  {f:12}: {r['known']:,} / {r['total']:,} ({r['pct']:.1f}%)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=None, help="PropORACLE repo root (default: parent of scripts/)")
    ap.add_argument("--verbose", action="store_true", help="Log each (sport, file_date) join group")
    ap.add_argument(
        "--from",
        dest="from_date",
        default="",
        metavar="YYYY-MM-DD",
        help="Minimum graded_props file_date (inclusive). Empty = all dates.",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Joined CSV path (default: <repo>/data/retrain_dataset.csv). "
        "Graded-only baseline is <stem>_graded_only<suffix> next to this file.",
    )
    args = ap.parse_args()
    root = _repo_root(args.repo_root)
    templates = root / "ui_runner" / "templates"
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    before_cov_df, after_cov_df = _load_graded_workbook_rows(root)
    before_cov = coverage_report(before_cov_df)
    after_cov = coverage_report(after_cov_df)
    _print_field_coverage("[retrain] Field coverage across all graded workbooks (before normalization):", before_cov)
    _print_field_coverage("[retrain] Field coverage across all graded workbooks (after normalization):", after_cov)

    df = load_all_graded_props(templates_dir=templates)
    if df.empty:
        print("No graded_props_*.json rows found.", file=sys.stderr)
        return 1

    df["result_u"] = df["result"].astype(str).str.strip().str.upper()
    decided = df[df["result_u"].isin(("HIT", "MISS"))].copy()
    decided["result_binary"] = (decided["result_u"] == "HIT").astype(int)

    from_s = str(args.from_date or "").strip()[:10]
    if from_s and len(from_s) == 10:
        before = len(decided)
        fd = decided["file_date"].astype(str).str.strip().str[:10]
        decided = decided.loc[fd >= from_s].copy()
        print(f"[filter] --from {from_s}: kept {len(decided):,}/{before:,} decided rows")
        if decided.empty:
            print("No rows left after --from filter.", file=sys.stderr)
            return 1

    graded_only_cols = [
        "file_date",
        "sport",
        "player",
        "prop",
        "line",
        "pick_type",
        "direction",
        "tier",
        "edge",
        "ml_prob",
        "result_binary",
        "result",
        "team",
        "opp_team",
        "actual_value",
        "margin",
        "void_reason",
        "pp_projection_id",
    ]
    for c in graded_only_cols:
        if c not in decided.columns:
            decided[c] = ""
    graded_out = decided[[c for c in graded_only_cols if c in decided.columns]]
    _pt_go = graded_out.get("pick_type", pd.Series("", index=graded_out.index))
    _dir_go = graded_out.get("direction", pd.Series("", index=graded_out.index))
    graded_out["pick_type_group"] = [normalize_pick_type_group(pt, d) for pt, d in zip(_pt_go, _dir_go, strict=False)]
    graded_out["def_tier_norm"] = graded_out.get("def_tier", pd.Series("", index=graded_out.index)).map(normalize_def_tier)
    fdg = graded_out["file_date"].astype(str).str.strip().str[:10]
    graded_out["tier_era"] = (fdg >= TIER_OVERHAUL_DATE).astype(int)
    if args.output is not None:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        graded_path = out_path.with_name(out_path.stem + "_graded_only" + out_path.suffix)
    else:
        out_path = data_dir / "retrain_dataset.csv"
        graded_path = data_dir / "retrain_dataset_graded_only.csv"
    graded_out.to_csv(graded_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {graded_path}  rows={len(graded_out):,}")

    # --- per (sport, file_date) step8 join ---
    sport_stats_map: dict[str, dict[str, int]] = {}
    joined_parts: list[pd.DataFrame] = []
    raw_cache: dict[tuple[str, str], pd.DataFrame | None] = {}

    def _cached_raw(sp: str, fd: str) -> pd.DataFrame | None:
        k = (str(sp), str(fd)[:10])
        if k not in raw_cache:
            raw_cache[k] = load_step8_dated_snapshot(root, sp, str(fd))
        return raw_cache[k]

    for (sport, file_date), g in decided.groupby(["sport", "file_date"], sort=True):
        g = g.copy()
        n_grad = len(g)
        if n_grad == 0:
            continue

        sk = "Soccer" if sport == "Soccer" else sport
        s8_raw = _cached_raw(sk, str(file_date))
        if s8_raw is None or len(s8_raw) == 0:
            if args.verbose:
                print(f"  [{sport} {file_date}] no step8 rows on disk — join skipped for {n_grad:,} rows")
            g2 = g.copy()
            for c in JOIN_FEATURE_COLS:
                g2[c] = np.nan
            g2["step8_game_date"] = ""
            g2["_joined"] = False
            joined_parts.append(g2)
            st = sport_stats_map.setdefault(str(sport), {"graded_decided": 0, "joined": 0})
            st["graded_decided"] += n_grad
            continue

        s8 = _prepare_step8(s8_raw, anchor_file_date=str(file_date))
        fd = pd.to_datetime(file_date, errors="coerce").normalize()
        _dd = (s8["_game_d"] - fd).abs()
        date_mask = s8["_game_d"].notna() & (_dd <= pd.Timedelta(days=1))
        s8 = s8.loc[date_mask]
        if len(s8) == 0:
            if args.verbose:
                print(f"  [{sport} {file_date}] step8 has no rows within ±1d of file_date — skipped {n_grad:,} rows")
            g2 = g.copy()
            for c in JOIN_FEATURE_COLS:
                g2[c] = np.nan
            g2["step8_game_date"] = ""
            g2["_joined"] = False
            joined_parts.append(g2)
            st = sport_stats_map.setdefault(str(sport), {"graded_decided": 0, "joined": 0})
            st["graded_decided"] += n_grad
            continue

        # graded_props JSON often omits PP pick type (em dash → ``standard``) while step8 has
        # goblin/demon. Join without pick_type for NHL/Soccer so step8 scores attach.
        sk_u = str(sk).upper()
        loose_pick = sk_u in ("NHL", "SOCCER")
        if loose_pick:
            sort_col = "rank_score" if "rank_score" in s8.columns else ("blended_score" if "blended_score" in s8.columns else None)
            if sort_col:
                s8 = s8.sort_values(sort_col, ascending=False, na_position="last")
            s8 = s8.drop_duplicates(subset=["_n_player", "_n_prop", "_n_line", "_n_dir"], keep="first")
        else:
            s8 = s8.drop_duplicates(
                subset=["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir", "_game_d"],
                keep="first",
            )

        g["_n_player"] = g["player"].map(player_join_key)
        g["_n_prop"] = g["prop"].map(prop_join_key)
        g["_n_line"] = g["line"].map(normalize_line)
        g["_n_pick"] = g["pick_type"].map(normalize_pick_type)
        g["_n_dir"] = g["direction"].map(normalize_direction)
        g["_file_d"] = pd.to_datetime(g["file_date"], errors="coerce").dt.normalize()

        merge_on = ["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir"]
        if loose_pick:
            merge_on = ["_n_player", "_n_prop", "_n_line", "_n_dir"]
        feat_cols = merge_on + ["_game_d"] + [c for c in JOIN_FEATURE_COLS if c in s8.columns]
        feat_cols = list(dict.fromkeys(feat_cols))
        feat = s8[[c for c in feat_cols if c in s8.columns]].copy()
        if "ml_prob" in s8.columns and "ml_prob" not in feat.columns:
            feat["_s8_ml_prob"] = pd.to_numeric(s8["ml_prob"], errors="coerce")

        m = g.merge(
            feat,
            on=merge_on,
            how="left",
        )
        for c in JOIN_FEATURE_COLS:
            if c not in m.columns:
                m[c] = np.nan
        _tol_d = (m["_file_d"] - m["_game_d"]).abs()
        tol = _tol_d <= pd.Timedelta(days=1)
        m["_tol"] = m["_game_d"].notna() & tol
        feat_present = pd.Series(False, index=m.index)
        for c in ("blended_score", "edge_score", "rank_score"):
            if c in m.columns:
                feat_present = feat_present | m[c].notna()
        m["_joined"] = m["_tol"] & feat_present
        for c in JOIN_FEATURE_COLS:
            if c not in m.columns:
                continue
            col = m[c]
            # bool / nullable-bool enrichment (NBA injury flags, MLB top_of_order) cannot take np.nan
            if pd.api.types.is_bool_dtype(col) or str(getattr(col, "dtype", "")) == "boolean":
                col = col.astype("float64")
            m[c] = col
            m.loc[~m["_joined"], c] = np.nan
        m["_date_diff"] = (m["_file_d"] - m["_game_d"]).abs().dt.days
        m = m.sort_values(["_joined", "_date_diff"], ascending=[False, True])
        dedupe_keys = ["file_date", "sport", "player", "prop", "line", "pick_type", "direction", "result_binary"]
        dedupe_keys = [k for k in dedupe_keys if k in m.columns]
        m = m.drop_duplicates(subset=dedupe_keys, keep="first")

        mp = pd.to_numeric(m["ml_prob"], errors="coerce") if "ml_prob" in m.columns else pd.Series(np.nan, index=m.index)
        if "_s8_ml_prob" in m.columns:
            mp = mp.fillna(pd.to_numeric(m["_s8_ml_prob"], errors="coerce"))
        m["ml_edge"] = pd.to_numeric(m["ml_edge"], errors="coerce")
        m["ml_edge"] = m["ml_edge"].where(m["ml_edge"].notna(), mp - 0.5)
        m = m.drop(columns=["_s8_ml_prob"], errors="ignore")

        joined_n = int(m["_joined"].sum()) if "_joined" in m.columns else 0
        pct = 100.0 * (n_grad - joined_n) / n_grad if n_grad else 0.0
        st = sport_stats_map.setdefault(str(sport), {"graded_decided": 0, "joined": 0})
        st["graded_decided"] += n_grad
        st["joined"] += joined_n
        if args.verbose:
            print(f"  [{sport} {file_date}] decided={n_grad:,} joined={joined_n:,} unjoined%={pct:.1f}")

        if "_game_d" in m.columns:
            m["step8_game_date"] = m["_game_d"].dt.strftime("%Y-%m-%d").where(m["_game_d"].notna(), "")
        drop_cols = [c for c in m.columns if c.startswith("_")]
        m = m.drop(columns=drop_cols, errors="ignore")
        joined_parts.append(m)

    out_df = pd.concat(joined_parts, ignore_index=True) if joined_parts else decided
    _pt_out = out_df.get("pick_type", pd.Series("", index=out_df.index))
    _dir_out = out_df.get("direction", pd.Series("", index=out_df.index))
    out_df["pick_type_group"] = [normalize_pick_type_group(pt, d) for pt, d in zip(_pt_out, _dir_out, strict=False)]
    out_df["def_tier_norm"] = out_df.get("def_tier", pd.Series("", index=out_df.index)).map(normalize_def_tier)
    fd_all = out_df["file_date"].astype(str).str.strip().str[:10]
    out_df["tier_era"] = (fd_all >= TIER_OVERHAUL_DATE).astype(int)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {out_path}  rows={len(out_df):,}")

    # Feature completeness on rows that successfully joined step8 scores (rank_score or legacy blended_score)
    jmask = pd.Series(False, index=out_df.index)
    for c in ("blended_score", "rank_score", "edge_score"):
        if c in out_df.columns:
            jmask = jmask | out_df[c].notna()
    if jmask.any():
        sub = out_df.loc[jmask, [c for c in JOIN_FEATURE_COLS if c in out_df.columns]]
        comp = {c: float(sub[c].notna().mean()) for c in sub.columns}
        print("Feature completeness (joined rows, non-null rate):", comp)

    print("\nJoin summary by sport (aggregated over all file_date groups):")
    for sp, st in sorted(sport_stats_map.items()):
        ng = st["graded_decided"]
        jn = st["joined"]
        up = round(100.0 * (ng - jn) / ng, 2) if ng else 0.0
        jr = round(100.0 * jn / ng, 2) if ng else 0.0
        print(f"  sport={sp}  decided={ng:,}  joined={jn:,}  join_rate%={jr}  unjoined%={up}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
