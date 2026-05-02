#!/usr/bin/env python3
"""
Build training CSVs from ui_runner/templates/graded_props_*.json joined to step8 slates.

Outputs:
  data/retrain_dataset.csv              — decided props + step8 features (left join)
  data/retrain_dataset_graded_only.csv  — decided props from JSON only (baseline)

Usage:
  py -3.14 scripts/build_retrain_dataset.py
  py -3.14 scripts/build_retrain_dataset.py --repo-root .
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
from grading.slate_grader import norm_player_key, norm_prop_key  # noqa: E402

STEP8_FEATURE_COLS = [
    "blended_score",
    "edge_score",
    "def_tier",
    "rank_score",
    "ml_edge",
    "deviation_level",
    "pp_projection_id",
]


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
    return str(s or "").strip().casefold()


def normalize_direction(s: Any) -> str:
    return str(s or "").strip().upper()


def _repo_root(arg: Path | None) -> Path:
    return Path(arg).resolve() if arg else Path(__file__).resolve().parent.parent


def _game_date_series(df: pd.DataFrame) -> pd.Series:
    ts = None
    if "game_date" in df.columns:
        ts = pd.to_datetime(df["game_date"], errors="coerce")
    elif "start_time" in df.columns:
        ts = pd.to_datetime(df["start_time"], errors="coerce")
    elif "Game Time" in df.columns:
        ts = pd.to_datetime(df["Game Time"], errors="coerce")
    if ts is None:
        return pd.Series(pd.NaT, index=df.index)
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    return ts.dt.normalize()


def _step8_prop_series(df: pd.DataFrame) -> pd.Series:
    for c in ("prop_norm", "prop_type_norm", "prop_type", "Prop Type", "Prop"):
        if c in df.columns:
            return df[c]
    return pd.Series([""] * len(df), index=df.index)


def _step8_direction_series(df: pd.DataFrame) -> pd.Series:
    if "final_bet_direction" in df.columns:
        return df["final_bet_direction"]
    if "bet_direction" in df.columns:
        return df["bet_direction"]
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
    }
    out = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
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
    if sport_u == "SOCCER" or sport == "Soccer":
        for p in (
            root / "Soccer" / "step8_soccer_direction.csv",
            root / "Soccer" / "outputs" / "step8_soccer_direction.csv",
        ):
            if p.is_file():
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
    return None


def prop_join_key(s: Any) -> str:
    """Align graded JSON `prop` text with step8 prop_norm / prop_type_norm."""
    return normalize_prop(norm_prop_key(s))


def player_join_key(s: Any) -> str:
    """Match slate_grader / backfill_graded_ml_columns player keys."""
    return str(norm_player_key(s) or "").casefold().strip()


def _prepare_step8(df: pd.DataFrame) -> pd.DataFrame:
    out = _canonicalize_step8_columns(df.copy())
    out["_n_player"] = out["player"].map(player_join_key) if "player" in out.columns else ""
    out["_n_prop"] = _step8_prop_series(out).map(prop_join_key)
    out["_n_line"] = out["line"].map(normalize_line) if "line" in out.columns else ""
    pt = _pick_col(out, ("pick_type", "Pick Type"))
    out["_n_pick"] = pt.map(normalize_pick_type)
    out["_n_dir"] = _step8_direction_series(out).map(normalize_direction)
    out["_game_d"] = _game_date_series(out)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=None, help="PropORACLE repo root (default: parent of scripts/)")
    ap.add_argument("--verbose", action="store_true", help="Log each (sport, file_date) join group")
    args = ap.parse_args()
    root = _repo_root(args.repo_root)
    templates = root / "ui_runner" / "templates"
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    df = load_all_graded_props(templates_dir=templates)
    if df.empty:
        print("No graded_props_*.json rows found.", file=sys.stderr)
        return 1

    df["result_u"] = df["result"].astype(str).str.strip().str.upper()
    decided = df[df["result_u"].isin(("HIT", "MISS"))].copy()
    decided["result_binary"] = (decided["result_u"] == "HIT").astype(int)

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
            for c in STEP8_FEATURE_COLS:
                g2[c] = np.nan
            g2["step8_game_date"] = ""
            g2["_joined"] = False
            joined_parts.append(g2)
            st = sport_stats_map.setdefault(str(sport), {"graded_decided": 0, "joined": 0})
            st["graded_decided"] += n_grad
            continue

        s8 = _prepare_step8(s8_raw)
        fd = pd.to_datetime(file_date, errors="coerce").normalize()
        _dd = (s8["_game_d"] - fd).abs()
        date_mask = s8["_game_d"].notna() & (_dd <= pd.Timedelta(days=1))
        s8 = s8.loc[date_mask]
        if len(s8) == 0:
            if args.verbose:
                print(f"  [{sport} {file_date}] step8 has no rows within ±1d of file_date — skipped {n_grad:,} rows")
            g2 = g.copy()
            for c in STEP8_FEATURE_COLS:
                g2[c] = np.nan
            g2["step8_game_date"] = ""
            g2["_joined"] = False
            joined_parts.append(g2)
            st = sport_stats_map.setdefault(str(sport), {"graded_decided": 0, "joined": 0})
            st["graded_decided"] += n_grad
            continue

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

        feat_cols = [
            "_n_player",
            "_n_prop",
            "_n_line",
            "_n_pick",
            "_n_dir",
            "_game_d",
        ] + [c for c in STEP8_FEATURE_COLS if c in s8.columns]
        feat = s8[feat_cols].copy()
        if "ml_prob" in s8.columns:
            feat["_s8_ml_prob"] = pd.to_numeric(s8["ml_prob"], errors="coerce")

        m = g.merge(
            feat,
            on=["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir"],
            how="left",
        )
        for c in STEP8_FEATURE_COLS:
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
        for c in STEP8_FEATURE_COLS:
            if c in m.columns:
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
    out_path = data_dir / "retrain_dataset.csv"
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {out_path}  rows={len(out_df):,}")

    # Feature completeness on joined rows
    jmask = out_df["blended_score"].notna() if "blended_score" in out_df.columns else pd.Series(False, index=out_df.index)
    if jmask.any():
        sub = out_df.loc[jmask, [c for c in STEP8_FEATURE_COLS if c in out_df.columns]]
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
