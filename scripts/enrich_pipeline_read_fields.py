#!/usr/bin/env python3
"""
Audit and export pipeline read-field completeness for a slate date.

Reads combined_slate_tickets_<date>.xlsx (Full Slate) or per-sport step8 files,
runs utils.pipeline_read_enrichment, and writes:
  data/reports/pipeline_read_audit_<date>.json
  data/reports/pipeline_read_enriched_<date>.csv  (optional)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.pipeline_read_enrichment import (  # noqa: E402
    CHECKLIST_PATH,
    SCHEMA_PATH,
    _mirror_stat_g_columns,
    audit_read_fields_dataframe,
    enrich_read_fields_dataframe,
    normalize_slate_column_names,
)

OUTPUTS_DIR = REPO_ROOT / "outputs"
REPORTS_DIR = REPO_ROOT / "data" / "reports"

_STAT_G_COLS = [f"stat_g{i}" for i in range(1, 11)]
_G_TO_STAT = {f"G{i}": f"stat_g{i}" for i in range(1, 11)}

STEP8_STAT_G_CANDIDATES: dict[str, list[Path]] = {
    **{
        k: [Path(str(p)) for p in v]
        for k, v in {
            "NBA": [
                OUTPUTS_DIR / "{d}" / "step8_nba_direction_clean_{d}.xlsx",
            ],
            "NBA1Q": [
                OUTPUTS_DIR / "{d}" / "step8_nba1q_direction_clean_{d}.xlsx",
            ],
            "NBA1H": [
                OUTPUTS_DIR / "{d}" / "step8_nba1h_direction_clean_{d}.xlsx",
            ],
            "MLB": [
                OUTPUTS_DIR / "{d}" / "mlb" / "step8_mlb_direction_clean.xlsx",
            ],
            "NHL": [
                OUTPUTS_DIR / "{d}" / "nhl" / "step8_nhl_direction_clean.xlsx",
                OUTPUTS_DIR / "{d}" / "step8_nhl_direction_clean_{d}.xlsx",
            ],
            "WNBA": [
                OUTPUTS_DIR / "{d}" / "wnba" / "step8_wnba_direction_clean.xlsx",
            ],
            "TENNIS": [
                OUTPUTS_DIR / "{d}" / "tennis" / "step8_tennis_direction_clean.xlsx",
                OUTPUTS_DIR / "{d}" / "step8_tennis_direction_clean_{d}.xlsx",
            ],
        }.items()
    },
}

STEP8_CANDIDATES: dict[str, list[Path]] = {
    "NBA": [
        OUTPUTS_DIR / "{d}" / "step8_nba_direction_clean_{d}.xlsx",
        REPO_ROOT / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
    ],
    "MLB": [
        OUTPUTS_DIR / "{d}" / "mlb" / "step8_mlb_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
    ],
    "NHL": [
        OUTPUTS_DIR / "{d}" / "step8_nhl_direction_clean_{d}.xlsx",
        REPO_ROOT / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
    ],
    "WNBA": [
        OUTPUTS_DIR / "{d}" / "wnba" / "step8_wnba_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "WNBA" / "outputs" / "step8_wnba_direction_clean.xlsx",
    ],
}


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
    return None


def _backfill_mlb_opponent_def_rank(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """Full Slate export omits Def Rank; merge from step8 MLB for checklist reads."""
    if df is None or len(df) == 0 or "sport" not in df.columns:
        return df
    sport_u = df["sport"].astype(str).str.upper()
    mlb_mask = sport_u.isin(["MLB", "BASEBALL"])
    if not mlb_mask.any():
        return df
    if "opponent_def_rank" in df.columns:
        filled = pd.to_numeric(df.loc[mlb_mask, "opponent_def_rank"], errors="coerce").notna()
        if bool(filled.all()):
            return df

    d = date_str.strip()[:10]
    src = _first_existing(
        [
            OUTPUTS_DIR / d / "mlb" / "step8_mlb_direction_clean.xlsx",
            REPO_ROOT / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
        ]
    )
    if src is None:
        return df
    try:
        s8 = normalize_slate_column_names(pd.read_excel(src))
    except Exception:
        return df
    if "opponent_def_rank" not in s8.columns:
        return df

    merge_keys = ["player", "prop_type", "line"]
    if not all(k in df.columns and k in s8.columns for k in merge_keys):
        return df

    lookup = s8[merge_keys + ["opponent_def_rank"]].drop_duplicates(subset=merge_keys)
    lookup = lookup.rename(columns={"opponent_def_rank": "_s8_def_rank"})
    lookup["_s8_def_rank"] = pd.to_numeric(lookup["_s8_def_rank"], errors="coerce")
    out = df.merge(lookup, on=merge_keys, how="left")
    if "opponent_def_rank" not in out.columns:
        out["opponent_def_rank"] = out["_s8_def_rank"]
    else:
        out["opponent_def_rank"] = pd.to_numeric(
            out["opponent_def_rank"], errors="coerce"
        ).fillna(out["_s8_def_rank"])
    return out.drop(columns=["_s8_def_rank"], errors="ignore")


def _backfill_wnba_rank_score(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """Full Slate may omit Rank Score on penalized void legs; merge from WNBA step8."""
    if df is None or len(df) == 0 or "sport" not in df.columns:
        return df
    sport_u = df["sport"].astype(str).str.upper()
    wnba_mask = sport_u.isin(["WNBA", "BASKETBALL_WNBA"])
    if not wnba_mask.any():
        return df
    rs = pd.to_numeric(df.get("rank_score"), errors="coerce")
    if rs is not None and bool(rs.loc[wnba_mask].notna().all()):
        return df

    d = date_str.strip()[:10]
    src = _first_existing(
        [
            OUTPUTS_DIR / d / "wnba" / "step8_wnba_direction_clean.xlsx",
            REPO_ROOT / "Sports" / "WNBA" / "outputs" / "step8_wnba_direction_clean.xlsx",
        ]
    )
    if src is None:
        return df
    try:
        s8 = normalize_slate_column_names(pd.read_excel(src))
    except Exception:
        return df
    if "rank_score" not in s8.columns:
        return df

    merge_keys = ["player", "prop_type", "line"]
    if not all(k in df.columns and k in s8.columns for k in merge_keys):
        return df

    extra = ["rank_score"]
    if "rank_score_penalized" in s8.columns:
        extra.append("rank_score_penalized")
    lookup = s8[merge_keys + extra].drop_duplicates(subset=merge_keys)
    lookup = lookup.rename(columns={c: f"_s8_{c}" for c in extra})
    for c in extra:
        if c == "rank_score":
            lookup[f"_s8_{c}"] = pd.to_numeric(lookup[f"_s8_{c}"], errors="coerce")
    out = df.merge(lookup, on=merge_keys, how="left")
    for c in extra:
        s8c = f"_s8_{c}"
        if c not in out.columns:
            out[c] = out[s8c]
        elif c == "rank_score":
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(out[s8c])
        else:
            out[c] = out[c].fillna(out[s8c])
    return out.drop(columns=[f"_s8_{c}" for c in extra], errors="ignore")


def _backfill_tennis_surface(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df is None or len(df) == 0 or "sport" not in df.columns:
        return df
    sport_u = df["sport"].astype(str).str.upper()
    t_mask = sport_u.isin(["TENNIS"])
    if not t_mask.any():
        return df
    if "surface" in df.columns:
        filled = df.loc[t_mask, "surface"].astype(str).str.strip().ne("")
        if bool(filled.all()):
            return df

    d = date_str.strip()[:10]
    patterns = [
        OUTPUTS_DIR / d / "tennis" / "step8_tennis_direction_clean.xlsx",
        OUTPUTS_DIR / d / f"step8_tennis_direction_clean_{d}.xlsx",
        OUTPUTS_DIR / d / "step8_tennis_direction_clean.xlsx",
        REPO_ROOT / "Sports" / "Tennis" / "outputs" / "step8_tennis_direction_clean.xlsx",
    ]
    src = _first_existing(patterns)
    if src is None:
        return df
    try:
        xl = pd.ExcelFile(src, engine="openpyxl")
        sheet = next(
            (s for s in ("Tennis", "ALL") if s in xl.sheet_names),
            xl.sheet_names[0],
        )
        s8 = normalize_slate_column_names(pd.read_excel(src, sheet_name=sheet, engine="openpyxl"))
    except Exception:
        return df
    if "surface" not in s8.columns:
        return df

    merge_keys = ["player", "prop_type", "line"]
    if not all(k in df.columns and k in s8.columns for k in merge_keys):
        return df

    lookup = s8[merge_keys + ["surface"]].drop_duplicates(subset=merge_keys)
    lookup = lookup.rename(columns={"surface": "_s8_surface"})
    out = df.merge(lookup, on=merge_keys, how="left")
    if "surface" not in out.columns:
        out["surface"] = out["_s8_surface"]
    else:
        empty = out["surface"].isna() | (out["surface"].astype(str).str.strip() == "")
        out.loc[empty, "surface"] = out.loc[empty, "_s8_surface"]
    return out.drop(columns=["_s8_surface"], errors="ignore")


def _backfill_nhl_line_combo(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    if df is None or len(df) == 0 or "sport" not in df.columns:
        return df
    sport_u = df["sport"].astype(str).str.upper()
    nhl_mask = sport_u.isin(["NHL", "ICEHOCKEY_NHL"])
    if not nhl_mask.any():
        return df
    if "line_combo" in df.columns:
        filled = df.loc[nhl_mask, "line_combo"].astype(str).str.strip().ne("")
        if bool(filled.all()):
            return df

    d = date_str.strip()[:10]
    src = _first_existing(
        [
            OUTPUTS_DIR / d / "nhl" / "step8_nhl_direction_clean.xlsx",
            OUTPUTS_DIR / d / f"step8_nhl_direction_clean_{d}.xlsx",
            REPO_ROOT / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
        ]
    )
    if src is None:
        return df
    try:
        s8 = normalize_slate_column_names(pd.read_excel(src))
    except Exception:
        return df
    if "line_combo" not in s8.columns:
        return df

    merge_keys = ["player", "prop_type", "line"]
    if not all(k in df.columns and k in s8.columns for k in merge_keys):
        return df

    lookup = s8[merge_keys + ["line_combo"]].drop_duplicates(subset=merge_keys)
    lookup = lookup.rename(columns={"line_combo": "_s8_line_combo"})
    out = df.merge(lookup, on=merge_keys, how="left")
    if "line_combo" not in out.columns:
        out["line_combo"] = out["_s8_line_combo"]
    else:
        empty = out["line_combo"].isna() | (out["line_combo"].astype(str).str.strip() == "")
        out.loc[empty, "line_combo"] = out.loc[empty, "_s8_line_combo"]
    return out.drop(columns=["_s8_line_combo"], errors="ignore")


def _normalize_stat_g_merge_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize player/prop/line keys for step8 ↔ Full Slate stat_g backfill."""
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    if "player" in out.columns:
        out["player"] = out["player"].astype(str).str.strip().str.lower()
    if "prop_type" in out.columns:
        out["prop_type"] = out["prop_type"].astype(str).str.strip().str.lower()
    if "line" in out.columns:
        out["line"] = pd.to_numeric(out["line"], errors="coerce").round(1)
    return out


def _read_step8_stat_g_lookup(path: Path) -> pd.DataFrame | None:
    """Load step8 board with stat_g1..10 for merge into Full Slate audit rows."""
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
        sheet = next(
            (s for s in ("ALL", "WNBA", "MLB", "NHL", "Tennis", "NBA1Q", "NBA1H", "NBA") if s in xl.sheet_names),
            xl.sheet_names[0],
        )
        part = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except Exception:
        return None
    part = part.rename(columns={k: v for k, v in _G_TO_STAT.items() if k in part.columns})
    part = normalize_slate_column_names(part)
    part = _mirror_stat_g_columns(part)
    merge_keys = ["player", "prop_type", "line"]
    have_g = [c for c in _STAT_G_COLS if c in part.columns]
    extra_cols = [c for c in ("distribution_std", "distribution_n") if c in part.columns]
    if not have_g or not all(k in part.columns for k in merge_keys):
        return None
    lookup = part[merge_keys + have_g + extra_cols].drop_duplicates(subset=merge_keys)
    for c in have_g:
        # Keep string game-log cells (Tennis em-dash sentinels) — numeric parse at merge time.
        lookup[c] = lookup[c].astype(str).str.strip().replace({"nan": "", "None": ""})
    for c in extra_cols:
        lookup[c] = pd.to_numeric(lookup[c], errors="coerce")
    return _normalize_stat_g_merge_keys(lookup)


def _backfill_stat_g_columns(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    Full Slate export omits stat_g* until Tier 3 export; merge from step8 for enrich audit.
    """
    if df is None or len(df) == 0 or "sport" not in df.columns:
        return df
    out = _mirror_stat_g_columns(df)

    d = date_str.strip()[:10]
    merge_keys = ["player", "prop_type", "line"]
    if not all(k in out.columns for k in merge_keys):
        return out

    sport_aliases: dict[str, tuple[str, ...]] = {
        "NBA": ("NBA",),
        "NBA1Q": ("NBA1Q",),
        "NBA1H": ("NBA1H",),
        "MLB": ("MLB", "BASEBALL"),
        "NHL": ("NHL", "ICEHOCKEY_NHL"),
        "WNBA": ("WNBA", "BASKETBALL_WNBA"),
        "TENNIS": ("TENNIS",),
    }

    for sport_key, aliases in sport_aliases.items():
        mask = out["sport"].astype(str).str.upper().isin(aliases)
        if not mask.any():
            continue
        if "stat_g1" in out.columns:
            sport_fill = (
                pd.to_numeric(out.loc[mask, "stat_g1"], errors="coerce").notna().mean()
            )
            if sport_fill >= 0.5:
                continue
        patterns = [Path(str(p).format(d=d)) for p in STEP8_STAT_G_CANDIDATES.get(sport_key, [])]
        src = _first_existing(patterns)
        if src is None:
            continue
        lookup = _read_step8_stat_g_lookup(src)
        if lookup is None or len(lookup) == 0:
            continue
        have_g = [c for c in _STAT_G_COLS if c in lookup.columns]
        dist_cols = [c for c in ("distribution_std", "distribution_n") if c in lookup.columns]
        merge_keys = ["player", "prop_type", "line"]
        sub = _normalize_stat_g_merge_keys(out.loc[mask, merge_keys])
        merged = sub.merge(
            lookup[merge_keys + have_g + dist_cols],
            on=merge_keys,
            how="left",
        )
        row_idx = out.index[mask]
        for c in have_g:
            from_lookup = merged[c].astype(str).str.strip()
            valid_lookup = from_lookup.notna() & ~from_lookup.isin(["", "nan", "None", "—", "-"])
            if c in out.columns:
                existing = out.loc[mask, c].astype(str).str.strip()
                out.loc[mask, c] = np.where(valid_lookup.to_numpy(), from_lookup, existing)
            else:
                out.loc[mask, c] = from_lookup.to_numpy()
        for c in dist_cols:
            merged_vals = pd.to_numeric(merged[c], errors="coerce")
            if c in out.columns:
                existing = pd.to_numeric(out.loc[mask, c], errors="coerce")
                merged_vals = merged_vals.where(merged_vals.notna(), existing)
            out.loc[mask, c] = merged_vals.to_numpy()

    return _mirror_stat_g_columns(out.loc[:, ~out.columns.duplicated()].copy())


def _apply_slate_backfills(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    df = _backfill_mlb_opponent_def_rank(df, date_str)
    df = _backfill_wnba_rank_score(df, date_str)
    df = _backfill_tennis_surface(df, date_str)
    df = _backfill_nhl_line_combo(df, date_str)
    df = _backfill_stat_g_columns(df, date_str)
    return df


def _load_combined_slate(date_str: str) -> pd.DataFrame | None:
    d = date_str.strip()[:10]
    candidates = [
        OUTPUTS_DIR / d / f"combined_slate_tickets_{d}.xlsx",
        REPO_ROOT / f"combined_slate_tickets_{d}.xlsx",
    ]
    path = _first_existing(candidates)
    if path is None:
        return None
    try:
        df = normalize_slate_column_names(pd.read_excel(path, sheet_name="Full Slate"))
    except Exception:
        try:
            df = normalize_slate_column_names(pd.read_excel(path, sheet_name=0))
        except Exception:
            return None
    return _apply_slate_backfills(df, date_str)


def _load_step8_fallback(date_str: str) -> pd.DataFrame:
    d = date_str.strip()[:10]
    frames: list[pd.DataFrame] = []
    for sport, patterns in STEP8_CANDIDATES.items():
        paths = [Path(str(p).format(d=d)) for p in patterns]
        src = _first_existing(paths)
        if src is None:
            continue
        try:
            part = pd.read_excel(src)
        except Exception:
            continue
        if part.empty:
            continue
        if "sport" not in part.columns:
            part = part.copy()
            part["sport"] = sport
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _missing_breakdown(enriched: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    if enriched is None or len(enriched) == 0:
        return counts
    col = enriched.get("read_fields_missing")
    if col is None:
        return counts
    for raw in col:
        try:
            miss = json.loads(str(raw)) if isinstance(raw, str) else (raw or [])
        except json.JSONDecodeError:
            miss = []
        if not isinstance(miss, list):
            continue
        for field in miss:
            key = str(field)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def run_audit(
    date_str: str,
    *,
    out_dir: Path | None = None,
    write_csv: bool = True,
    sport_filter: str | None = None,
) -> Path:
    reports = out_dir or REPORTS_DIR
    reports.mkdir(parents=True, exist_ok=True)

    df = _load_combined_slate(date_str)
    source = "combined_slate_full"
    if df is None or len(df) == 0:
        df = _load_step8_fallback(date_str)
        source = "step8_fallback"

    if df is None or len(df) == 0:
        payload = {
            "date": date_str[:10],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "source": "none",
            "rows": 0,
            "error": "no slate data found",
            "checklist_path": str(CHECKLIST_PATH.relative_to(REPO_ROOT)),
            "schema_path": str(SCHEMA_PATH.relative_to(REPO_ROOT)),
        }
        out_path = reports / f"pipeline_read_audit_{date_str[:10]}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[pipeline_read] WARN: no data for {date_str}; wrote {out_path}")
        return out_path

    enriched = enrich_read_fields_dataframe(df)
    audit = audit_read_fields_dataframe(enriched, sport=sport_filter)

    checklist = json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))
    expected_sports = sorted(
        {
            str(k).upper()
            for k, spec in (checklist.get("sports") or {}).items()
            for alias in [k, *(spec.get("aliases") or [])]
        }
    )
    active = set((audit.get("sports") or {}).keys())
    audit["expected_sports"] = expected_sports
    audit["inactive_sports"] = [s for s in expected_sports if s not in active]
    ineligible = 0
    if "pick_type_eligible" in enriched.columns:
        ineligible = int((~enriched["pick_type_eligible"].astype(bool)).sum())

    payload = {
        "date": date_str[:10],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": source,
        "rows": int(len(enriched)),
        "pick_type_ineligible_rows": ineligible,
        "sports": audit.get("sports") or {},
        "expected_sports": audit.get("expected_sports") or [],
        "inactive_sports": audit.get("inactive_sports") or [],
        "top_missing_fields": _missing_breakdown(enriched),
        "checklist_path": str(CHECKLIST_PATH.relative_to(REPO_ROOT)),
        "schema_path": str(SCHEMA_PATH.relative_to(REPO_ROOT)),
    }

    out_json = reports / f"pipeline_read_audit_{date_str[:10]}.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[pipeline_read] audit -> {out_json}  ({payload['rows']} rows, source={source})")

    if write_csv:
        export_cols = [
            c
            for c in [
                "sport",
                "player",
                "prop_type",
                "direction",
                "pick_type",
                "line",
                "edge",
                "rank_score",
                "tier",
                "hit_prob_over",
                "hit_prob_under",
                "hit_prob_selected",
                "hit_prob_actionable",
                "leg_prob_used",
                "prop_quality_score",
                "rank_read_score",
                "data_completeness_score",
                "pick_type_eligible",
                "distribution_std",
                "distribution_n",
                "std_norm",
                "confidence_score",
                "calibration_bucket",
                "calibration_actual_hit_rate",
                "calibration_n",
                "read_fields_missing",
            ]
            if c in enriched.columns
        ]
        out_csv = reports / f"pipeline_read_enriched_{date_str[:10]}.csv"
        enriched[export_cols].to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"[pipeline_read] csv   -> {out_csv}")

    for sp, stats in (payload.get("sports") or {}).items():
        print(
            f"  {sp}: rows={stats.get('rows')} "
            f"eligible={stats.get('pick_type_eligible_pct')}% "
            f"completeness={stats.get('avg_data_completeness')} "
            f"P(over)={stats.get('avg_hit_prob_over')} "
            f"P(action)={stats.get('avg_hit_prob_actionable')} "
            f"calib_bucket={stats.get('pct_filled_calibration_bucket')}%"
        )

    return out_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit pipeline read-field population.")
    parser.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    parser.add_argument("--out-dir", default="", help="Report directory (default: data/reports)")
    parser.add_argument("--sport", default="", help="Optional sport filter (e.g. MLB)")
    parser.add_argument("--no-csv", action="store_true", help="Skip enriched CSV export")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else REPORTS_DIR
    sport = args.sport.strip() or None
    run_audit(
        args.date,
        out_dir=out_dir,
        write_csv=not args.no_csv,
        sport_filter=sport,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
