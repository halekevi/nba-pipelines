#!/usr/bin/env python3
"""
step4b_attach_wnba_context.py — WNBA usage%, pace, star tier, fouls, B2B context.

Run after step4_fetch_player_stats.py:
  py -3.14 scripts/step4b_attach_wnba_context.py \\
    --input outputs/<date>/wnba/step4_wnba_stats.csv \\
    --output outputs/<date>/wnba/step4_wnba_stats.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
from wnba_stats_api import (
    ensure_caches,
    foul_trouble_risk,
    pace_context,
    usage_tier,
)
from herhoopstats_client import load_player_index

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STAR_TIERS_CSV = _DATA_DIR / "wnba_star_tiers.csv"

log = logging.getLogger("wnba.step4b")

TEAM_ALIAS = {
    "CONN": "CON", "CONNECTICUT": "CON", "SUN": "CON",
    "NY": "NYL", "LIBERTY": "NYL",
    "LV": "LVA", "LAS VEGAS": "LVA", "ACES": "LVA",
    "LA": "LAS", "SPARKS": "LAS",
    "PHX": "PHO", "MERCURY": "PHO",
    "PHOENIX": "PHO",
    "WASH": "WSH", "WASHINGTON": "WSH", "MYS": "WSH",
    "MINN": "MIN", "LYNX": "MIN",
    "CHI": "CHI", "SKY": "CHI",
    "DAL": "DAL", "WINGS": "DAL",
    "ATL": "ATL", "DREAM": "ATL",
    "SEA": "SEA", "STORM": "SEA",
    "IND": "IND", "FEVER": "IND",
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_team(v: object) -> str:
    s = str(v or "").strip().upper()
    if not s or s == "NAN":
        return ""
    return TEAM_ALIAS.get(s, s)


def _player_name(row: pd.Series) -> str:
    for c in ("player_name", "player"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def _scale_usage(usg: object) -> float | None:
    if usg is None or (isinstance(usg, float) and np.isnan(usg)):
        return None
    try:
        u = float(usg)
        if u > 1.0:
            u /= 100.0
        return round(u, 4)
    except (TypeError, ValueError):
        return None


def load_star_tiers() -> dict[str, dict]:
    if not STAR_TIERS_CSV.exists():
        return {}
    df = pd.read_csv(STAR_TIERS_CSV, encoding="utf-8-sig")
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        name = str(r.get("player_name", "")).strip()
        if not name:
            continue
        out[_norm_name(name)] = {
            "star_tier": int(r.get("star_tier", 2)),
            "team": _norm_team(r.get("team_abbreviation", "")),
        }
    return out


def _derive_b2b(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "b2b_flag" not in out.columns:
        out["b2b_flag"] = False
    if "game_date" not in out.columns:
        return out
    out["_gd"] = pd.to_datetime(out["game_date"], errors="coerce")
    out["b2b_flag"] = False
    pname_col = next((c for c in ("player_name", "player") if c in out.columns), None)
    if not pname_col:
        out.drop(columns=["_gd"], errors="ignore", inplace=True)
        return out
    for pname, grp in out.groupby(out[pname_col].astype(str)):
        idx = grp.sort_values("_gd").index
        prev = None
        for i in idx:
            gd = out.at[i, "_gd"]
            if pd.isna(gd):
                continue
            if prev is not None and (gd - prev).days == 1:
                out.at[i, "b2b_flag"] = True
            prev = gd
    out.drop(columns=["_gd"], errors="ignore", inplace=True)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_wnba_stats.csv")
    ap.add_argument("--output", default="step4_wnba_stats.csv")
    ap.add_argument("--season", default="", help="WNBA season year e.g. 2025")
    ap.add_argument("--refresh", action="store_true", help="Force refresh WNBA API caches")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    season = str(args.season).strip()
    if not season:
        gdates = pd.to_datetime(df.get("game_date", pd.Series(dtype=object)), errors="coerce")
        if gdates.notna().any():
            season = str(int(gdates.dropna().dt.year.mode().iloc[0]))
        else:
            season = "2025"

    if args.refresh:
        from wnba_stats_api import refresh_usage_cache, refresh_pace_cache, refresh_foul_cache
        log.info("Refreshing WNBA Stats API caches for season %s...", season)
        refresh_usage_cache(season)
        refresh_pace_cache(season)
        refresh_foul_cache(season)

    usage_cache, pace_cache, foul_cache = ensure_caches(season)
    ukey = f"season_{season}"
    usage_players = (usage_cache.get(ukey) or {}).get("players") or {}
    pace_teams = (pace_cache.get(ukey) or {}).get("teams") or {}
    foul_players = (foul_cache.get(ukey) or {}).get("players") or {}
    star_map = load_star_tiers()
    hhs_map = load_player_index(season)

    new_cols = [
        "usage_pct", "usage_tier", "min_per_game",
        "team_pace", "opp_pace", "pace_delta", "pace_context",
        "star_tier", "is_franchise_star",
        "foul_rate_per_36", "foul_trouble_risk",
        "b2b_flag", "wnba_b2b_weight", "b2b_rest_context",
        "hhs_efg_pct", "hhs_ts_pct", "hhs_per",
        "wnba_context_source",
    ]
    for c in new_cols:
        if c not in df.columns:
            df[c] = np.nan
    for c in ("usage_tier", "pace_context", "foul_trouble_risk", "b2b_rest_context", "wnba_context_source"):
        df[c] = df[c].astype(object)
    df["star_tier"] = 2
    df["is_franchise_star"] = False
    df["wnba_b2b_weight"] = 1.0

    df = _derive_b2b(df)

    usage_hit = pace_hit = star_hit = foul_hit = 0
    n = len(df)

    for idx, row in df.iterrows():
        pname = _player_name(row)
        pnorm = _norm_name(pname)
        team = _norm_team(row.get("team", ""))
        opp = _norm_team(row.get("opp_team", ""))
        sources: list[str] = []

        # Usage
        urec = usage_players.get(f"{pname}|{team}") or usage_players.get(f"{pname}|")
        if not urec:
            for k, v in usage_players.items():
                if _norm_name(v.get("player_name", "")) == pnorm:
                    urec = v
                    break
        if urec:
            usg = _scale_usage(urec.get("usage_pct"))
            if usg is not None:
                df.at[idx, "usage_pct"] = usg
                df.at[idx, "usage_tier"] = usage_tier(usg)
                usage_hit += 1
            mpg = urec.get("min_per_game")
            if mpg is not None:
                df.at[idx, "min_per_game"] = mpg
                if pd.isna(row.get("stat_last5_avg")) or str(row.get("minutes_tier", "")).upper() == "UNKNOWN":
                    pass
            sources.append("wnba_api_usage")

        # Pace
        tpace = (pace_teams.get(team) or {}).get("pace")
        opace = (pace_teams.get(opp) or {}).get("pace")
        if tpace is not None:
            df.at[idx, "team_pace"] = tpace
            pace_hit += 1
        if opace is not None:
            df.at[idx, "opp_pace"] = opace
        if tpace is not None or opace is not None:
            tp = float(tpace) if tpace is not None else None
            op = float(opace) if opace is not None else None
            if tp is not None and op is not None:
                df.at[idx, "pace_delta"] = tp - op
            df.at[idx, "pace_context"] = pace_context(tp, op)
            sources.append("wnba_api_pace")

        # Star tier (always default 2)
        st = star_map.get(pnorm)
        if st:
            df.at[idx, "star_tier"] = int(st["star_tier"])
        else:
            df.at[idx, "star_tier"] = 2
        star_hit += 1
        df.at[idx, "is_franchise_star"] = int(df.at[idx, "star_tier"]) == 1

        # Fouls
        frec = foul_players.get(f"{pname}|{team}")
        if not frec:
            for k, v in foul_players.items():
                if _norm_name(v.get("player_name", "")) == pnorm:
                    frec = v
                    break
        if frec and frec.get("pf") is not None and frec.get("min"):
            pf, mn = float(frec["pf"]), float(frec["min"])
            if mn > 0:
                df.at[idx, "foul_rate_per_36"] = round((pf / mn) * 36.0, 3)
                df.at[idx, "foul_trouble_risk"] = foul_trouble_risk(pf, mn)
                foul_hit += 1
                sources.append("wnba_api_fouls")

        # Her Hoop Stats supplemental
        hhs = hhs_map.get(pnorm)
        if hhs:
            for src_col, dst in (("hhs_efg_pct", "hhs_efg_pct"), ("hhs_ts_pct", "hhs_ts_pct"), ("hhs_per", "hhs_per")):
                if hhs.get(src_col) is not None:
                    df.at[idx, dst] = hhs.get(src_col)
            sources.append("herhoopstats")

        # B2B
        b2b = bool(row.get("b2b_flag")) if "b2b_flag" in row.index else False
        if isinstance(df.at[idx, "b2b_flag"], (bool, np.bool_)) and df.at[idx, "b2b_flag"]:
            b2b = True
        elif str(df.at[idx, "b2b_flag"]).strip().lower() in ("1", "true", "yes"):
            b2b = True
        df.at[idx, "b2b_flag"] = b2b
        df.at[idx, "wnba_b2b_weight"] = 1.5 if b2b else 1.0
        df.at[idx, "b2b_rest_context"] = "b2b_second" if b2b else "normal_rest"

        # Fill min_per_game from step4 if still empty
        if pd.isna(df.at[idx, "min_per_game"]) or df.at[idx, "min_per_game"] == "":
            for mc in ("min_player_avg", "avg_minutes", "stat_last5_avg"):
                if mc in df.columns:
                    v = pd.to_numeric(row.get(mc), errors="coerce")
                    if pd.notna(v):
                        df.at[idx, "min_per_game"] = float(v)
                        break

        df.at[idx, "wnba_context_source"] = ";".join(sources) if sources else ""

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="WNBA",
        repo_root=_REPO_ROOT,
    )

    print(f"WNBA context attached: {n} rows")
    print(f"  usage_pct: {usage_hit}/{n} ({usage_hit/n:.1%})")
    print(f"  team_pace: {pace_hit}/{n} ({pace_hit/n:.1%})")
    print(f"  star_tier: {star_hit}/{n} (100% — default tier 2 when unknown)")
    print(f"  foul_trouble_risk: {foul_hit}/{n} ({foul_hit/n:.1%})")
    if usage_hit < n * 0.5:
        print("  [HINT] Low usage fill — run with --refresh when WNBA Stats API is up (in-season).")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ WNBA step4b failed. {type(e).__name__}: {e}")
        sys.exit(1)
