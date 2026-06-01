#!/usr/bin/env python3
"""
NFL step3 — merge ESPN defense ranks (step4) + optional team last-N form (step4b) onto step2.

Adds:
  - opp_pass_def_rank  (opponent pass defense rank; 1 = stingiest)
  - team_pass_def_rank (player's team pass defense rank)
  - opp_rush_def_rank / team_rush_def_rank (when reference defense CSV is used)
  - points_allowed_pg_opp (optional context)
  - opp_sacks_rank / opp_to_rank (optional; step7 can use later)
  - team_last5_* / opp_last5_* when --team-form CSV is present (from step4b_team_last5_games.py)

Defense source (--defense-source):
  auto      — prefer data/reference/nfl_team_defense.csv when newer than legacy CSV
  reference — data/reference/nfl_team_defense.csv (repo root)
  legacy    — NFL/data/defense_rankings.csv from step4_defense_rankings.py

Run from NFL/ with NFL_PIPELINE_ACTIVE=1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_NFL_ROOT = _SCRIPT_DIR.parent
_REPO_ROOT = _SCRIPT_DIR.resolve().parents[2]
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

LEGACY_DEFENSE = _NFL_ROOT / "data" / "defense_rankings.csv"
REFERENCE_DEFENSE = _REPO_ROOT / "data" / "reference" / "nfl_team_defense.csv"


def _abbr(x: object) -> str:
    return str(x or "").strip().upper()


# Align slate abbreviations with ESPN scoreboard (step4b)
_SLATE_ABBR = {
    "LA": "LAR",
    "WAS": "WSH",
    "JAC": "JAX",
}


def _abbr_form(x: object) -> str:
    a = _abbr(x)
    return _SLATE_ABBR.get(a, a)


def resolve_defense_path(source: str, legacy: Path, reference: Path) -> tuple[Path, str]:
    """Return (path, label) for the defense CSV to load."""
    mode = str(source or "auto").strip().lower()
    if mode == "legacy":
        return legacy, "legacy"
    if mode == "reference":
        return reference, "reference"
    if mode != "auto":
        raise SystemExit(f"Unknown --defense-source: {source} (use auto|legacy|reference)")

    if reference.is_file() and legacy.is_file():
        if reference.stat().st_mtime >= legacy.stat().st_mtime:
            return reference, "reference (auto)"
        return legacy, "legacy (auto)"
    if reference.is_file():
        return reference, "reference (auto)"
    if legacy.is_file():
        return legacy, "legacy (auto)"
    return legacy, "legacy (auto, missing)"


def load_defense_table(path: Path) -> pd.DataFrame:
    """Normalize legacy or reference defense CSV to a common team-keyed frame."""
    raw = pd.read_csv(path, encoding="utf-8-sig")
    df = raw.copy()

    if "team_abbr" in df.columns:
        df["team"] = df["team_abbr"].map(_abbr)
    elif "team" in df.columns:
        df["team"] = df["team"].map(_abbr)
    else:
        raise SystemExit(f"Defense CSV missing team/team_abbr: {path}")

    if "pass_def_rank" not in df.columns:
        raise SystemExit(f"Defense CSV missing pass_def_rank: {path}")

    if "rush_def_rank" not in df.columns:
        df["rush_def_rank"] = pd.NA

    if "points_allowed_pg" not in df.columns:
        df["points_allowed_pg"] = pd.NA

    keep = ["team", "pass_def_rank", "rush_def_rank", "points_allowed_pg"]
    for opt in ("sacks_rank", "to_rank"):
        if opt in df.columns:
            keep.append(opt)
    return df[keep].drop_duplicates(subset=["team"], keep="first")


def _merge_team_form(df: pd.DataFrame, form_path: Path, team_col: str, opp_col: str) -> pd.DataFrame:
    form = pd.read_csv(form_path, encoding="utf-8-sig")
    if "team" not in form.columns:
        print("[NFL step3] team form CSV missing 'team' column; skipping")
        return df
    form = form.copy()
    form["team"] = form["team"].map(_abbr_form)

    def _pref(pfx: str) -> pd.DataFrame:
        ren = {c: f"{pfx}_{c}" for c in form.columns if c != "team"}
        ren["team"] = f"_{pfx}_join"
        return form.rename(columns=ren)

    tj = _pref("team")
    out = df.merge(tj, left_on=df[team_col].map(_abbr_form), right_on="_team_join", how="left")
    out = out.drop(columns=["_team_join"], errors="ignore")
    oj = _pref("opp")
    out = out.merge(oj, left_on=out[opp_col].map(_abbr_form), right_on="_opp_join", how="left")
    out = out.drop(columns=["_opp_join"], errors="ignore")
    print(f"[NFL step3] merged team form from {form_path} ({len(form)} teams)")
    return out


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step2_clean_props.csv")
    ap.add_argument(
        "--defense",
        default="",
        help="Override defense CSV path (default: resolved via --defense-source).",
    )
    ap.add_argument(
        "--defense-source",
        choices=("auto", "legacy", "reference"),
        default="auto",
        help="auto prefers reference nfl_team_defense.csv when newer than legacy.",
    )
    ap.add_argument(
        "--team-form",
        default="data/nfl_team_last5.csv",
        help="CSV from step4b_team_last5_games.py; omit or set empty to skip.",
    )
    ap.add_argument("--output", default="data/outputs/step3_nfl_with_defense.csv")
    args = ap.parse_args()

    slate = Path(args.input)
    if not slate.is_file():
        print(f"[NFL step3] Missing slate: {slate}")
        sys.exit(1)

    if str(args.defense or "").strip():
        deff = Path(args.defense)
        src_label = "override"
    else:
        deff, src_label = resolve_defense_path(
            args.defense_source, LEGACY_DEFENSE, REFERENCE_DEFENSE
        )

    if not deff.is_file():
        print(f"[NFL step3] Missing defense CSV: {deff} (source={src_label})")
        sys.exit(1)

    print(f"[NFL step3] Defense source: {src_label} -> {deff}")

    df = pd.read_csv(slate, encoding="utf-8-sig")
    if df.empty:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[NFL step3] Wrote empty {out}")
        return

    dref = load_defense_table(deff)
    dmap_pass = dref.set_index("team")["pass_def_rank"].to_dict()
    dmap_rush = dref.set_index("team")["rush_def_rank"].to_dict()
    pts_map = dref.set_index("team")["points_allowed_pg"].to_dict()
    sacks_map = (
        dref.set_index("team")["sacks_rank"].to_dict() if "sacks_rank" in dref.columns else {}
    )
    to_map = dref.set_index("team")["to_rank"].to_dict() if "to_rank" in dref.columns else {}

    team_col = "team" if "team" in df.columns else None
    opp_col = "opp_team" if "opp_team" in df.columns else ("opponent" if "opponent" in df.columns else None)
    if not team_col or not opp_col:
        print("[NFL step3] slate needs team + opp_team (or opponent) columns")
        sys.exit(1)

    t = df[team_col].map(_abbr)
    o = df[opp_col].map(_abbr)
    df["team_pass_def_rank"] = t.map(lambda x: dmap_pass.get(x, pd.NA))
    df["opp_pass_def_rank"] = o.map(lambda x: dmap_pass.get(x, pd.NA))
    df["team_rush_def_rank"] = t.map(lambda x: dmap_rush.get(x, pd.NA))
    df["opp_rush_def_rank"] = o.map(lambda x: dmap_rush.get(x, pd.NA))
    df["points_allowed_pg_opp"] = o.map(lambda x: pts_map.get(x, pd.NA))
    if sacks_map:
        df["opp_sacks_rank"] = o.map(lambda x: sacks_map.get(x, pd.NA))
    if to_map:
        df["opp_to_rank"] = o.map(lambda x: to_map.get(x, pd.NA))

    tf = str(args.team_form or "").strip()
    if tf:
        form_p = Path(tf)
        if not form_p.is_file():
            form_p = _NFL_ROOT / tf
        if form_p.is_file():
            df = _merge_team_form(df, form_p, team_col, opp_col)
        else:
            print(f"[NFL step3] team form not found ({tf}); skip last-5 merge")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[NFL step3] Wrote {out_path} rows={len(df)}")
    # TODO Phase 2: def_tier from utils.defense_tiers using opp_pass_def_rank / points_allowed_pg_opp


if __name__ == "__main__":
    main()
