#!/usr/bin/env python3
"""
step4b_attach_nba_context.py — NBA usage%, pace, role type, minutes certainty, positional defense.

Run after step4_attach_player_stats_espn_cache.py (in-place on step4_with_stats.csv).
"""

from __future__ import annotations

import argparse
import ast
import logging
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = Path(__file__).resolve().parent
_SCRIPTS_ROOT = _REPO_ROOT / "scripts"
for p in (_REPO_ROOT, _SCRIPTS_ROOT, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from role_stability import role_stability
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
from nba_stats_api import (
    derive_usage_role_type,
    ensure_caches,
    nba_pace_context,
    norm_team,
    position_group_from_pos,
    positional_matchup_tier,
    usage_tier,
)

log = logging.getLogger("nba.step4b")


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _player_name(row: pd.Series) -> str:
    for c in ("player_name", "player"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def _scale_pct(v: object) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        x = float(v)
        if x > 1.0 and x <= 100.0:
            x /= 100.0
        return round(x, 4)
    except (TypeError, ValueError):
        return None


def _parse_minutes_list(val: object) -> list[float]:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return []
    if isinstance(val, list):
        return [float(x) for x in val if x is not None and float(x) > 0]
    s = str(val).strip()
    if not s or s.lower() in ("nan", "[]"):
        return []
    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, list):
            return [float(x) for x in parsed if x is not None and float(x) > 0]
    except (ValueError, SyntaxError):
        pass
    return []


def _attach_minutes_certainty(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in (
        "minutes_mean_L10",
        "minutes_std_L10",
        "minutes_cv_L10",
        "minutes_floor_L10",
        "minutes_ceil_L10",
    ):
        if c not in out.columns:
            out[c] = np.nan

    for idx, row in out.iterrows():
        mins = _parse_minutes_list(row.get("minutes_L10_list"))
        if len(mins) < 3:
            existing = row.get("role_stability_score")
            if pd.notna(existing):
                score = float(existing)
                out.at[idx, "role_stability_score"] = score
                out.at[idx, "high_variance_role"] = score < 0.35
            continue
        arr = np.array(mins, dtype=float)
        mean = float(arr.mean())
        std = float(arr.std()) if len(arr) > 1 else 0.0
        cv = std / mean if mean > 0 else 1.0
        score = role_stability(mins)
        out.at[idx, "minutes_mean_L10"] = round(mean, 2)
        out.at[idx, "minutes_std_L10"] = round(std, 2)
        out.at[idx, "minutes_cv_L10"] = round(cv, 4)
        out.at[idx, "minutes_floor_L10"] = round(float(np.percentile(arr, 10)), 2)
        out.at[idx, "minutes_ceil_L10"] = round(float(np.percentile(arr, 90)), 2)
        if score is not None:
            out.at[idx, "role_stability_score"] = score
            out.at[idx, "high_variance_role"] = bool(score < 0.35)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_with_stats.csv")
    ap.add_argument("--output", default="step4_with_stats.csv")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    season = str(args.season).strip() or "2025-26"

    if args.refresh:
        from nba_stats_api import refresh_usage_cache, refresh_pace_cache, refresh_opp_defense_cache

        log.info("Refreshing NBA Stats API caches for %s...", season)
        refresh_usage_cache(season)
        refresh_pace_cache(season)
        refresh_opp_defense_cache(season)

    usage_cache, pace_cache, opp_cache = ensure_caches(season)
    ukey = f"season_{season}"
    usage_players = (usage_cache.get(ukey) or {}).get("players") or {}
    pace_teams = (pace_cache.get(ukey) or {}).get("teams") or {}
    opp_entries = (opp_cache.get(ukey) or {}).get("entries") or {}

    # League ranks for positional matchup tiers
    pts_vals = [e["pts_allowed"] for e in opp_entries.values() if e.get("pts_allowed") is not None]
    reb_vals = [e["reb_allowed"] for e in opp_entries.values() if e.get("reb_allowed") is not None]
    ast_vals = [e["ast_allowed"] for e in opp_entries.values() if e.get("ast_allowed") is not None]

    str_cols = (
        "usage_tier",
        "usage_role_type",
        "pace_context",
        "positional_matchup_tier",
        "nba_context_source",
    )
    for c in str_cols:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = df[c].astype(object)

    num_cols = (
        "usage_pct",
        "reb_pct",
        "ast_pct",
        "min_per_game",
        "pie",
        "team_pace",
        "opp_pace",
        "game_pace",
        "pace_delta",
        "opp_def_rating",
        "opp_pts_allowed_vs_position",
        "opp_reb_allowed_vs_position",
        "opp_ast_allowed_vs_position",
    )
    for c in num_cols:
        if c not in df.columns:
            df[c] = np.nan

    df["usage_role_type"] = "role_player"
    df["positional_matchup_tier"] = "neutral"

    df = _attach_minutes_certainty(df)

    usage_hit = pace_hit = pos_hit = 0
    n = len(df)

    for idx, row in df.iterrows():
        pname = _player_name(row)
        pnorm = _norm_name(pname)
        team = norm_team(row.get("team", ""))
        opp = norm_team(row.get("opp_team", ""))
        sources: list[str] = []

        urec = None
        for _pid, rec in usage_players.items():
            if _norm_name(rec.get("player_name", "")) == pnorm and (
                not team or norm_team(rec.get("team", "")) == team
            ):
                urec = rec
                break
        if urec is None:
            for _pid, rec in usage_players.items():
                if _norm_name(rec.get("player_name", "")) == pnorm:
                    urec = rec
                    break

        if urec:
            usg = _scale_pct(urec.get("usage_pct"))
            ast = _scale_pct(urec.get("ast_pct"))
            reb = _scale_pct(urec.get("reb_pct"))
            if usg is not None:
                df.at[idx, "usage_pct"] = usg
                df.at[idx, "usage_tier"] = usage_tier(usg)
                usage_hit += 1
            if ast is not None:
                df.at[idx, "ast_pct"] = ast
            if reb is not None:
                df.at[idx, "reb_pct"] = reb
            mpg = urec.get("min_per_game")
            if mpg is not None:
                df.at[idx, "min_per_game"] = float(mpg)
            pie = urec.get("pie")
            if pie is not None:
                df.at[idx, "pie"] = float(pie)
            df.at[idx, "usage_role_type"] = derive_usage_role_type(usg, ast, reb)
            sources.append("nba_api_usage")

        tpace = (pace_teams.get(team) or {}).get("pace")
        opace = (pace_teams.get(opp) or {}).get("pace")
        odef = (pace_teams.get(opp) or {}).get("def_rating")
        if tpace is not None:
            df.at[idx, "team_pace"] = float(tpace)
            pace_hit += 1
        if opace is not None:
            df.at[idx, "opp_pace"] = float(opace)
        if odef is not None:
            df.at[idx, "opp_def_rating"] = float(odef)
        if tpace is not None or opace is not None:
            tp = float(tpace) if tpace is not None else None
            op = float(opace) if opace is not None else None
            if tp is not None and op is not None:
                df.at[idx, "game_pace"] = (tp + op) / 2.0
                df.at[idx, "pace_delta"] = tp - op
                df.at[idx, "pace_context"] = nba_pace_context(df.at[idx, "game_pace"])
            sources.append("nba_api_pace")

        pos_grp = position_group_from_pos(row.get("pos", urec.get("position") if urec else ""))
        okey = f"{opp}_{pos_grp}_{season}"
        orec = opp_entries.get(okey)
        if orec:
            pts_a = orec.get("pts_allowed")
            reb_a = orec.get("reb_allowed")
            ast_a = orec.get("ast_allowed")
            if pts_a is not None:
                df.at[idx, "opp_pts_allowed_vs_position"] = float(pts_a)
            if reb_a is not None:
                df.at[idx, "opp_reb_allowed_vs_position"] = float(reb_a)
            if ast_a is not None:
                df.at[idx, "opp_ast_allowed_vs_position"] = float(ast_a)
            prop_n = str(row.get("prop_norm", row.get("prop_type", ""))).lower()
            allowed = None
            league = pts_vals
            if "reb" in prop_n:
                allowed, league = reb_a, reb_vals
            elif "ast" in prop_n:
                allowed, league = ast_a, ast_vals
            else:
                allowed, league = pts_a, pts_vals
            df.at[idx, "positional_matchup_tier"] = positional_matchup_tier(
                prop_n, allowed, league
            )
            pos_hit += 1
            sources.append("nba_api_pos_def")

        df.at[idx, "nba_context_source"] = ";".join(sources) if sources else ""

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="NBA",
        repo_root=_REPO_ROOT,
    )

    print(f"NBA context attached: {n} rows")
    print(f"  usage_pct: {usage_hit}/{n} ({usage_hit / max(n, 1):.1%})")
    print(f"  team_pace: {pace_hit}/{n} ({pace_hit / max(n, 1):.1%})")
    print(f"  positional_matchup_tier: {pos_hit}/{n} (default neutral when unknown)")
    print(f"  usage_role_type: 100% (default role_player)")
    if usage_hit < n * 0.5:
        print("  [HINT] Low usage fill — run with --refresh when stats.nba.com is reachable.")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ NBA step4b failed. {type(e).__name__}: {e}")
        sys.exit(1)
