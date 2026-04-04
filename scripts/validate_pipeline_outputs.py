#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd


REQUIRED = ["player", "team", "opp", "prop_type", "pick_type", "line", "direction", "hit_rate"]
TEXT_FIELDS = ["player", "team", "opp", "prop_type", "pick_type", "direction"]
NUM_FIELDS = ["line", "hit_rate", "rank_score"]


@dataclass
class SourceDef:
    sport: str
    path: str
    sheet: Optional[str] = None
    required: bool = False


def _is_blank(v) -> bool:
    if v is None:
        return True
    s = str(v).strip().lower()
    return s in ("", "nan", "none", "null", "nat")


def _combo_mask(df: pd.DataFrame) -> pd.Series:
    """
    Combo exemptions apply only when is_combo_player is explicitly true.
    """
    if "is_combo_player" in df.columns:
        return (
            df["is_combo_player"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin(("1", "true", "yes", "y"))
        )
    return pd.Series(False, index=df.index)


def _read_any(path: str, preferred_sheet: Optional[str]) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        xls = pd.ExcelFile(path, engine="openpyxl")
        sheet = preferred_sheet if preferred_sheet in xls.sheet_names else (
            "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
        )
        return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    return pd.read_csv(path)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    ren = {
        "Player": "player",
        "player_norm": "player",
        "Team": "team",
        "team_abbr": "team",
        "pp_team": "team",
        "Opp": "opp",
        "opp_team": "opp",
        "opp_team_abbr": "opp",
        "pp_opp_team": "opp",
        "opponent": "opp",
        "Prop": "prop_type",
        "prop_norm": "prop_type",
        "Pick Type": "pick_type",
        "Line": "line",
        "Direction": "direction",
        "Hit Rate (5g)": "hit_rate",
        "Hit Rate (10g)": "hit_rate",
        "Composite Hit Rate": "hit_rate",
        "composite_hr": "hit_rate",
        "line_hit_rate_over_ou_5": "hit_rate",
        "line_hit_rate_over_ou_10": "hit_rate",
        "Rank Score": "rank_score",
        "line_hit_rate": "hit_rate",
        "final_bet_direction": "direction",
        "recommended_side": "direction",
        "stat_type": "prop_type",
        "player_name": "player",
    }
    out = df.rename(columns=ren).copy()
    out = out.loc[:, ~out.columns.duplicated()].copy()
    return out


def evaluate_source(src: SourceDef, max_blank: float, min_hr_cov: float) -> dict:
    if not os.path.exists(src.path):
        if src.required:
            return {"sport": src.sport, "path": src.path, "exists": False, "passed": False, "failures": ["missing_file"]}
        return {"sport": src.sport, "path": src.path, "exists": False, "passed": True, "warnings": ["optional_missing_file"], "failures": []}

    df = _normalize(_read_any(src.path, src.sheet))
    if df.empty:
        if src.required:
            return {"sport": src.sport, "path": src.path, "exists": True, "rows": 0, "passed": False, "failures": ["empty_dataframe"]}
        return {"sport": src.sport, "path": src.path, "exists": True, "rows": 0, "passed": True, "warnings": ["optional_empty_dataframe"], "failures": []}

    out = {
        "sport": src.sport,
        "path": src.path,
        "exists": True,
        "rows": int(len(df)),
        "missing_required_cols": [],
        "blank_rate_by_field": {},
        "numeric_null_rate_by_field": {},
        "hit_rate_coverage": 0.0,
        "failures": [],
        "passed": True,
    }
    combo_rows = _combo_mask(df)
    non_combo_rows = ~combo_rows
    out["combo_row_count"] = int(combo_rows.sum())
    out["non_combo_row_count"] = int(non_combo_rows.sum())

    miss = [c for c in REQUIRED if c not in df.columns]
    out["missing_required_cols"] = miss
    if miss:
        out["failures"].append("missing_required_cols:" + ",".join(miss))

    for c in TEXT_FIELDS:
        if c not in df.columns:
            continue
        # Combo props can legitimately have gaps in some upstream fields.
        # Validate blank-rate thresholds on non-combo rows only.
        sample = df.loc[non_combo_rows, c] if non_combo_rows.any() else df[c]
        rate = float(sample.map(_is_blank).mean())
        out["blank_rate_by_field"][c] = round(rate, 4)
        if rate > max_blank:
            out["failures"].append(f"blank_rate>{max_blank}:{c}={rate:.3f}")

    for c in NUM_FIELDS:
        if c not in df.columns:
            continue
        n = pd.to_numeric(df[c], errors="coerce")
        out["numeric_null_rate_by_field"][c] = round(float(n.isna().mean()), 4)

    if "hit_rate" in df.columns:
        hr_sample = df.loc[non_combo_rows, "hit_rate"] if non_combo_rows.any() else df["hit_rate"]
        hr = pd.to_numeric(hr_sample, errors="coerce")
        cov = float(hr.notna().mean())
        out["hit_rate_coverage"] = round(cov, 4)
        if cov < min_hr_cov:
            out["failures"].append(f"hit_rate_coverage<{min_hr_cov}:hit_rate={cov:.3f}")

    out["passed"] = len(out["failures"]) == 0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate upstream sport pipeline outputs before combine.")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--repo-root", default="")
    ap.add_argument("--max-blank-rate", type=float, default=0.10)
    ap.add_argument("--min-hit-rate-coverage", type=float, default=0.60)
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args()

    repo_root = args.repo_root.strip() or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    d = args.date

    cbb_dated = os.path.join(repo_root, "CBB", "outputs", d, "step6_ranked_cbb.xlsx")
    cbb_fallback = os.path.join(repo_root, "CBB", "step6_ranked_cbb.xlsx")
    cbb_path = cbb_dated if os.path.exists(cbb_dated) else cbb_fallback

    sources = [
        SourceDef("NBA", os.path.join(repo_root, "NBA", "data", "outputs", "step8_all_direction_clean.xlsx"), "ALL", True),
        SourceDef("CBB", cbb_path, "ALL", True),
        SourceDef("NHL", os.path.join(repo_root, "NHL", "outputs", "step8_nhl_direction_clean.xlsx"), "All Props", False),
        SourceDef("Soccer", os.path.join(repo_root, "Soccer", "outputs", "step8_soccer_direction_clean.xlsx"), "ALL", False),
        SourceDef("MLB", os.path.join(repo_root, "MLB", "step8_mlb_direction_clean.xlsx"), "ALL", False),
        SourceDef("NBA1Q", os.path.join(repo_root, "NBA", "step8_nba1q_direction_clean.xlsx"), "ALL", False),
        SourceDef("NBA1H", os.path.join(repo_root, "NBA", "step8_nba1h_direction_clean.xlsx"), "ALL", False),
        SourceDef("WCBB", os.path.join(repo_root, "CBB", "step6_ranked_wcbb.xlsx"), "ALL", False),
    ]

    results = []
    for s in sources:
        r = evaluate_source(s, args.max_blank_rate, args.min_hit_rate_coverage)
        results.append(r)
        status = "PASS" if r.get("passed") else "FAIL"
        print(f"[DQ {s.sport}] {status} rows={r.get('rows', 0)} path={s.path}")
        for w in r.get("warnings", []):
            print(f"  - {w}")
        for f in r.get("failures", []):
            print(f"  - {f}")

    out_dir = os.path.join(repo_root, "outputs", d)
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"upstream_data_quality_{d}.json")
    csv_path = os.path.join(out_dir, f"upstream_data_quality_{d}.csv")

    payload = {
        "date": d,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "max_blank_rate": args.max_blank_rate,
            "min_hit_rate_coverage": args.min_hit_rate_coverage,
            "warn_only": args.warn_only,
        },
        "results": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    pd.json_normalize(results).to_csv(csv_path, index=False)
    print(f"[DQ] JSON -> {json_path}")
    print(f"[DQ] CSV  -> {csv_path}")

    failed = [r["sport"] for r in results if not r.get("passed")]
    if failed and not args.warn_only:
        print("[DQ] FAILED sports: " + ", ".join(failed))
        return 2
    if failed:
        print("[DQ] WARN-ONLY mode; continuing despite failures.")
    else:
        print("[DQ] All sports passed upstream validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
