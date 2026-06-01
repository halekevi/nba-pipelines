#!/usr/bin/env python3
"""
Attach conference + national pass/rush offense & defense ranks, YDS/G averages, and
prop-aware OVERALL_DEF_RANK / def_tier to the CFB slate.

Reads: data/reference/cfb_team_unit_rankings.csv (from build_cfb_unit_rankings.py)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[4]
_CFB_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

REFRESH_SCRIPT = _REPO / "scripts" / "refresh_rankings.py"

from utils.cfb_playoff_metadata import CFB_TEAM_ALIASES, norm_cfb_team_abbr
from utils.defense_tiers import def_tier_from_overall_rank, normalize_def_tier_label

# Conference-scoped (existing column names on rankings CSV)
CONF_RANK_FIELDS = (
    ("pass_off_rank", "pass_off_tier"),
    ("rush_off_rank", "rush_off_tier"),
    ("pass_def_rank", "pass_def_tier"),
    ("rush_def_rank", "rush_def_tier"),
)

NAT_RANK_FIELDS = (
    ("pass_off_rank_nat", "pass_off_tier_nat"),
    ("rush_off_rank_nat", "rush_off_tier_nat"),
    ("pass_def_rank_nat", "pass_def_tier_nat"),
    ("rush_def_rank_nat", "rush_def_tier_nat"),
    ("overall_off_rank_nat", "overall_off_tier_nat"),
    ("overall_def_rank_nat", "overall_def_tier_nat"),
)

AVG_FIELDS = (
    "pass_off_yds_pg",
    "rush_off_yds_pg",
    "pass_def_yds_pg",
    "rush_def_yds_pg",
    "total_off_yds_pg",
    "total_def_yds_pg",
)


def _norm_key(x: object) -> str:
    return norm_cfb_team_abbr(x)


def _normalize_rankings_csv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map build_cfb_unit_rankings.py national schema (off_*/def_*) to legacy attach keys.
    Conference ranks are not produced anymore; national ranks are mirrored for compat.
    """
    if "off_pass_rank" not in df.columns:
        return df

    out = df.copy()
    n = len(out)
    rank_map = (
        ("off_pass_rank", "pass_off_rank_nat", "pass_off_tier_nat", "pass_off_rank", "pass_off_tier"),
        ("off_rush_rank", "rush_off_rank_nat", "rush_off_tier_nat", "rush_off_rank", "rush_off_tier"),
        ("def_pass_rank", "pass_def_rank_nat", "pass_def_tier_nat", "pass_def_rank", "pass_def_tier"),
        ("def_rush_rank", "rush_def_rank_nat", "rush_def_tier_nat", "rush_def_rank", "rush_def_tier"),
        ("off_points_rank", "overall_off_rank_nat", "overall_off_tier_nat", None, None),
        ("def_points_rank", "overall_def_rank_nat", "overall_def_tier_nat", None, None),
    )
    ypg_pairs = (
        ("off_pass_ypg", "pass_off_yds_pg"),
        ("off_rush_ypg", "rush_off_yds_pg"),
        ("def_pass_ypg_allowed", "pass_def_yds_pg"),
        ("def_rush_ypg_allowed", "rush_def_yds_pg"),
        ("off_points_pg", "total_off_yds_pg"),
        ("def_points_allowed_pg", "total_def_yds_pg"),
    )

    def _tier_from_rank(r: object) -> str:
        if str(r).strip() in ("", "nan"):
            return ""
        return def_tier_from_overall_rank(int(float(r)), n)

    for src, nat_r, nat_t, conf_r, conf_t in rank_map:
        if src not in out.columns:
            continue
        out[nat_r] = out[src]
        out[nat_t] = out[src].map(_tier_from_rank)
        if conf_r and conf_t:
            out[conf_r] = out[src]
            out[conf_t] = out[nat_t]

    for src, dst in ypg_pairs:
        if src in out.columns:
            out[dst] = out[src]

    return out


def _maybe_refresh_cfb_rankings(rank_path: Path, season: int = 0) -> None:
    """Refresh reference rankings via scripts/refresh_rankings.py when stale (>7 days)."""
    if REFRESH_SCRIPT.is_file():
        cmd = [sys.executable, str(REFRESH_SCRIPT), "--sport", "cfb"]
        if season:
            cmd.extend(["--season", str(int(season))])
        proc = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.returncode != 0 and proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        return

    build_script = _CFB_ROOT / "scripts" / "build_cfb_unit_rankings.py"
    if not rank_path.is_file() and build_script.is_file():
        print("[CFB step3] Rankings missing — running build_cfb_unit_rankings.py")
        cmd = [sys.executable, str(build_script)]
        if season:
            cmd.extend(["--season", str(int(season))])
        cmd.extend(["--out", str(rank_path)])
        proc = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.returncode != 0 and proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)


def _load_lookup(path: Path) -> dict[str, dict]:
    df = _normalize_rankings_csv(pd.read_csv(path, dtype=str).fillna(""))
    by_abbr: dict[str, dict] = {}
    for _, r in df.iterrows():
        abbr = _norm_key(r.get("team_abbr", ""))
        if not abbr:
            continue
        payload = {c: r[c] for c in df.columns if c != "team_abbr"}
        by_abbr[abbr] = payload
        for alias, canon in CFB_TEAM_ALIASES.items():
            if _norm_key(canon) == abbr:
                by_abbr[_norm_key(alias)] = payload
    return by_abbr


def _attach_side(
    df: pd.DataFrame,
    abbr_col: str,
    prefix: str,
    lookup: dict[str, dict],
    field_groups: tuple[tuple[str, str], ...],
    avg_fields: tuple[str, ...] = (),
) -> None:
    for rank_col, tier_col in field_groups:
        out_rank = f"{prefix}_{rank_col}"
        out_tier = f"{prefix}_{tier_col}"
        ranks, tiers = [], []
        for _, row in df.iterrows():
            abbr = _norm_key(row.get(abbr_col, ""))
            payload = lookup.get(abbr)
            if not payload:
                ranks.append("")
                tiers.append("")
                continue
            ranks.append(payload.get(rank_col, ""))
            t = payload.get(tier_col, "")
            tiers.append(normalize_def_tier_label(t) or str(t).strip())
        df[out_rank] = ranks
        df[out_tier] = tiers

    for avg_col in avg_fields:
        out_avg = f"{prefix}_{avg_col}"
        avgs = []
        for _, row in df.iterrows():
            abbr = _norm_key(row.get(abbr_col, ""))
            payload = lookup.get(abbr) or {}
            avgs.append(payload.get(avg_col, ""))
        df[out_avg] = avgs


def _prop_def_keys(prop: str) -> tuple[str, str, str, str]:
    """Return (opp_def_rank_nat, opp_def_tier_nat, opp_def_avg_col, matchup_tier_col) for prop."""
    p = re.sub(r"\s+", "", str(prop or "").lower())
    if "pass" in p or p in ("pass_yds", "pass_td", "passingyards", "passingtds"):
        return (
            "pass_def_rank_nat",
            "pass_def_tier_nat",
            "pass_def_yds_pg",
            "opp_pass_def_tier_nat",
        )
    if "rush" in p or p in ("rush_yds", "rush_td", "rushingyards", "rushingtds"):
        return (
            "rush_def_rank_nat",
            "rush_def_tier_nat",
            "rush_def_yds_pg",
            "opp_rush_def_tier_nat",
        )
    if "rec" in p or p in ("rec_yds", "rec_td", "receptions", "receivingyards"):
        return (
            "pass_def_rank_nat",
            "pass_def_tier_nat",
            "pass_def_yds_pg",
            "opp_pass_def_tier_nat",
        )
    return (
        "overall_def_rank_nat",
        "overall_def_tier_nat",
        "total_def_yds_pg",
        "opp_overall_def_tier_nat",
    )


def _attach_prop_def_context(
    df: pd.DataFrame,
    opp_col: str,
    prop_col: str,
    lookup: dict[str, dict],
) -> None:
    """Map opponent national def rank/tier + allowed YDS/G avg onto each prop row."""
    ranks, tiers, avgs, n_teams = [], [], [], []
    for _, row in df.iterrows():
        prop = str(row.get(prop_col, "") or row.get("prop_type", ""))
        rk, tr, avg_col, _ = _prop_def_keys(prop)
        payload = lookup.get(_norm_key(row.get(opp_col, "")))
        if not payload:
            ranks.append("")
            tiers.append("")
            avgs.append("")
            n_teams.append("")
            continue
        ranks.append(payload.get(rk, ""))
        t = payload.get(tr, "")
        tiers.append(normalize_def_tier_label(t) or str(t).strip())
        avgs.append(payload.get(avg_col, ""))
        n_teams.append(str(len(lookup)))

    df["OVERALL_DEF_RANK"] = ranks
    df["OPP_OVERALL_DEF_RANK"] = ranks
    df["def_tier"] = tiers
    df["opp_def_tier"] = tiers
    df["opp_def_yds_pg_avg"] = avgs
    df["fbs_team_count"] = n_teams


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument(
        "--rankings",
        default="data/reference/cfb_team_unit_rankings.csv",
        help="Unit rankings CSV from build_cfb_unit_rankings.py",
    )
    ap.add_argument("--output", required=True)
    ap.add_argument("--season", type=int, default=0, help="CFB season for rankings refresh (0 = auto).")
    ap.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Do not auto-refresh stale cfb_team_unit_rankings.csv.",
    )
    args = ap.parse_args()

    cfb_root = Path(__file__).resolve().parents[2]
    rank_path = Path(args.rankings)
    if not rank_path.is_absolute():
        rank_path = cfb_root / rank_path
    if not args.skip_refresh:
        _maybe_refresh_cfb_rankings(rank_path, season=int(args.season))
    if not rank_path.exists():
        raise SystemExit(
            f"Missing rankings file: {rank_path}\n"
            "Run: py -3.14 scripts/build_cfb_unit_rankings.py --season <year>"
        )

    df = pd.read_csv(args.input, dtype=str).fillna("")
    lookup = _load_lookup(rank_path)
    print(f"→ Rankings: {rank_path} | teams={len(lookup)}")

    team_col = next((c for c in ("team_abbr", "pp_team", "team") if c in df.columns), None)
    opp_col = next((c for c in ("opp_team_abbr", "pp_opp_team", "opp") if c in df.columns), None)
    prop_col = next((c for c in ("prop_norm", "prop_type") if c in df.columns), "prop_type")
    if not team_col:
        raise SystemExit(f"No team column in slate. Columns: {list(df.columns)}")

    all_rank_fields = CONF_RANK_FIELDS + NAT_RANK_FIELDS
    _attach_side(df, team_col, "team", lookup, all_rank_fields, AVG_FIELDS)
    if opp_col:
        _attach_side(df, opp_col, "opp", lookup, all_rank_fields, AVG_FIELDS)
        if "team_pass_off_rank" in df.columns and "opp_pass_def_rank" in df.columns:
            df["matchup_pass_off_vs_def_rank"] = df["team_pass_off_rank"]
            df["matchup_pass_off_vs_def_tier"] = df["opp_pass_def_tier"]
            df["matchup_rush_off_vs_def_rank"] = df["team_rush_off_rank"]
            df["matchup_rush_off_vs_def_tier"] = df["opp_rush_def_tier"]
            df["matchup_pass_off_vs_def_tier_nat"] = df["opp_pass_def_tier_nat"]
            df["matchup_rush_off_vs_def_tier_nat"] = df["opp_rush_def_tier_nat"]
        _attach_prop_def_context(df, opp_col, prop_col, lookup)

    misses = 0
    if team_col:
        for _, row in df.iterrows():
            if _norm_key(row.get(team_col, "")) not in lookup:
                misses += 1
    filled_def = int((df.get("def_tier", pd.Series(dtype=str)).astype(str).str.strip() != "").sum())
    print(f"→ Rows={len(df)} | team rank misses={misses} | def_tier filled={filled_def}")

    df.to_csv(args.output, index=False)
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    main()
