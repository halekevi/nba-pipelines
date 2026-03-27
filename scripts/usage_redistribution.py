#!/usr/bin/env python3
"""
usage_redistribution.py

Compute per-player usage boosts from injured teammates and attach:
  - usage_boost
  - usage_boost_proj
  - usage_boost_reason
  - usage_boost_source
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

USAGE_ROLE_WEIGHTS = {
    "PRIMARY": 0.28,
    "SECONDARY": 0.20,
    "ROLE": 0.12,
    "BENCH": 0.06,
    "UNKNOWN": 0.10,
}

STATUS_WEIGHTS = {
    "out": 1.00,
    "injured reserve": 1.00,
    "doubtful": 0.75,
    "day-to-day": 0.40,
    "questionable": 0.50,
}

SPORT_ROLE_FIELD = {
    "NBA": "usage_role",
    "NBA1Q": "usage_role",
    "NBA1H": "usage_role",
    "SOCCER": "usage_role",
    "CBB": None,
    "WCBB": None,
    "NHL": "player_role",
    "MLB": "player_type_norm",
    "WNBA": "usage_role",
}

NHL_ROLE_MAP = {
    "SKATER": "SECONDARY",
    "GOALIE": "UNKNOWN",
}

MLB_ROLE_MAP = {
    "starter": "PRIMARY",
    "reliever": "ROLE",
    "batter": "SECONDARY",
    "pitcher": "PRIMARY",
}

BOOST_TO_STAT_MULTIPLIERS = {
    "points": 18.0,
    "rebounds": 8.0,
    "assists": 5.0,
    "pts+rebs+asts": 31.0,
    "pts+rebs": 26.0,
    "pts+asts": 23.0,
    "3-pt made": 2.5,
    "steals": 1.5,
    "blocked shots": 1.2,
    "turnovers": 2.0,
    "strikeouts": 0.0,
    "shots on goal": 2.0,
    "goals": 0.4,
    "saves": 0.0,
}

MAX_USAGE_BOOST = 0.08


def _norm_prop_label(v: object) -> str:
    s = str(v or "").strip().lower().replace("_", " ")
    s = " ".join(s.split())
    aliases = {
        "pts": "points",
        "reb": "rebounds",
        "ast": "assists",
        "pra": "pts+rebs+asts",
        "pr": "pts+rebs",
        "pa": "pts+asts",
        "ra": "rebs+asts",
        "3pm": "3-pt made",
        "fg3m": "3-pt made",
        "blk": "blocked shots",
        "stl": "steals",
        "tov": "turnovers",
    }
    return aliases.get(s, s)


def _canon_sport(sport: str) -> str:
    s = str(sport or "").strip().upper()
    if s in {"SOCCER", "NHL", "MLB", "NBA", "CBB", "WCBB", "WNBA", "NBA1Q", "NBA1H"}:
        return s
    return s


def _injury_file_sport(sport: str) -> str:
    s = _canon_sport(sport)
    if s in {"NBA1Q", "NBA1H"}:
        return "nba"
    return s.lower()


def _norm_name(name: object) -> str:
    return " ".join(str(name or "").lower().strip().split())


def _norm_team(team: object) -> str:
    return str(team or "").strip().upper()


def _fuzzy_match(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()


def _resolve_role(raw_role: object, sport: str) -> str:
    s = _canon_sport(sport)
    rv = str(raw_role or "").strip()
    if not rv:
        return "UNKNOWN"
    if s == "NHL":
        return NHL_ROLE_MAP.get(rv.upper(), "UNKNOWN")
    if s == "MLB":
        return MLB_ROLE_MAP.get(rv.lower(), "UNKNOWN")
    role = rv.upper()
    if role in USAGE_ROLE_WEIGHTS:
        return role
    return "UNKNOWN"


def _status_weight(status: object) -> float:
    st = str(status or "").strip().lower()
    return float(STATUS_WEIGHTS.get(st, 0.0))


def _get_role_series(df: pd.DataFrame, sport: str) -> pd.Series:
    field = SPORT_ROLE_FIELD.get(_canon_sport(sport), None)
    if field and field in df.columns:
        return df[field]
    return pd.Series(["UNKNOWN"] * len(df), index=df.index)


def _match_on_slate(team_rows: pd.DataFrame, injury_player: str) -> bool:
    ip = _norm_name(injury_player)
    if not ip:
        return False
    for p in team_rows.get("player", pd.Series([], dtype=str)).astype(str):
        if _fuzzy_match(ip, p) >= 0.85:
            return True
    return False


def _injured_out_ir_names(inj_df: pd.DataFrame, team: str) -> List[str]:
    t = _norm_team(team)
    if inj_df.empty:
        return []
    out = []
    team_df = inj_df[inj_df["_team_norm"] == t]
    for _, r in team_df.iterrows():
        st = str(r.get("injury_status", "")).strip().lower()
        if st in {"out", "injured reserve"}:
            out.append(str(r.get("player", "")).strip())
    return out


def apply_usage_redistribution(
    slate_df: pd.DataFrame,
    sport: str,
    date: str,
    repo_root: str,
) -> pd.DataFrame:
    out = slate_df.copy()
    out["usage_boost"] = 0.0
    out["usage_boost_proj"] = 0.0
    out["usage_boost_reason"] = ""
    out["usage_boost_source"] = "none"
    if out.empty:
        return out

    root = Path(repo_root)
    inj_sport = _injury_file_sport(sport)
    inj_path = root / "outputs" / str(date) / f"injuries_{inj_sport}_{date}.csv"
    if not inj_path.exists():
        return out

    try:
        inj = pd.read_csv(inj_path, dtype=str).fillna("")
    except Exception:
        return out
    if inj.empty or "team" not in inj.columns or "player" not in inj.columns:
        return out

    inj["_team_norm"] = inj["team"].map(_norm_team)
    inj["_status_w"] = inj["injury_status"].map(_status_weight)

    role_series = _get_role_series(out, sport)
    out["_role_resolved"] = role_series.map(lambda x: _resolve_role(x, sport))
    out["_team_norm"] = out.get("team", pd.Series([""] * len(out), index=out.index)).map(_norm_team)
    out["_player_norm"] = out.get("player", pd.Series([""] * len(out), index=out.index)).map(_norm_name)

    reasons_by_idx: Dict[int, List[str]] = {int(i): [] for i in out.index}

    for team in sorted(set(out["_team_norm"].tolist())):
        if not team:
            continue
        team_rows = out[out["_team_norm"] == team]
        if team_rows.empty:
            continue
        inj_team = inj[inj["_team_norm"] == team]
        if inj_team.empty:
            continue

        unavailable = _injured_out_ir_names(inj_team, team)

        for _, ir in inj_team.iterrows():
            status_w = float(ir.get("_status_w", 0.0) or 0.0)
            if status_w <= 0.0:
                continue
            inj_player = str(ir.get("player", "")).strip()
            if not inj_player:
                continue
            # Skip if this injured player is itself present on slate.
            if _match_on_slate(team_rows, inj_player):
                continue

            injured_role = "UNKNOWN"
            best_ratio = 0.0
            for _, tr in team_rows.iterrows():
                ratio = _fuzzy_match(inj_player, tr.get("player", ""))
                if ratio > best_ratio:
                    best_ratio = ratio
                    injured_role = str(tr.get("_role_resolved", "UNKNOWN"))

            vacated = USAGE_ROLE_WEIGHTS.get(injured_role, 0.10) * status_w
            if vacated <= 0:
                continue

            recipients = []
            weights = []
            for idx, tr in team_rows.iterrows():
                pname = str(tr.get("player", "")).strip()
                if not pname:
                    continue
                if any(_fuzzy_match(pname, nm) >= 0.85 for nm in unavailable):
                    continue
                role = str(tr.get("_role_resolved", "UNKNOWN"))
                w = USAGE_ROLE_WEIGHTS.get(role, 0.10)
                recipients.append(int(idx))
                weights.append(float(w))
            total_w = float(sum(weights))
            if total_w <= 0 or not recipients:
                continue

            status_txt = str(ir.get("injury_status", "")).strip()
            for ridx, rw in zip(recipients, weights):
                boost = vacated * (rw / total_w)
                prev = float(out.at[ridx, "usage_boost"] or 0.0)
                rem = max(0.0, MAX_USAGE_BOOST - prev)
                boost = min(float(boost), rem)
                if boost <= 0:
                    continue
                out.at[ridx, "usage_boost"] = round(prev + boost, 6)
                reasons_by_idx[ridx].append(
                    f"{inj_player} ({status_txt}, {injured_role}) -> +{boost:.3f} usage"
                )

    for idx, r in out.iterrows():
        ub = float(r.get("usage_boost", 0.0) or 0.0)
        if ub <= 0:
            continue
        pn = _norm_prop_label(r.get("prop_norm", r.get("prop_type", "")))
        mult = float(BOOST_TO_STAT_MULTIPLIERS.get(pn, 0.0))
        out.at[idx, "usage_boost_proj"] = round(ub * mult, 6)
        out.at[idx, "usage_boost_reason"] = " | ".join(reasons_by_idx.get(int(idx), []))
        out.at[idx, "usage_boost_source"] = "espn_injury"

    out = out.drop(columns=[c for c in ("_role_resolved", "_team_norm", "_player_norm") if c in out.columns])
    return out


def _load_test_slate(repo_root: Path) -> pd.DataFrame:
    csv_path = repo_root / "NBA" / "data" / "outputs" / "step8_all_direction.csv"
    if csv_path.exists():
        return pd.read_csv(csv_path, low_memory=False).fillna("")
    xlsx_path = repo_root / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx"
    if xlsx_path.exists():
        return pd.read_excel(xlsx_path, sheet_name=0).fillna("")
    return pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--sport", default="NBA")
    ap.add_argument("--date", default="")
    ap.add_argument("--repo-root", default="")
    args = ap.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parents[1]
    run_date = str(args.date or pd.Timestamp.today().strftime("%Y-%m-%d"))[:10]

    if args.test:
        df = _load_test_slate(repo_root)
        if df.empty:
            print("[usage] No test slate found.")
            return
        out = apply_usage_redistribution(df, args.sport, run_date, str(repo_root))
        boosted = out[pd.to_numeric(out["usage_boost"], errors="coerce").fillna(0) > 0].copy()
        boosted = boosted.sort_values("usage_boost", ascending=False)
        cols = [c for c in ["player", "team", "prop_type", "usage_boost", "usage_boost_proj", "usage_boost_reason"] if c in boosted.columns]
        print(f"[usage] boosted rows: {len(boosted)}")
        if len(boosted):
            print(boosted[cols].head(5).to_string(index=False))
        return

    print("[usage] module loaded. Use apply_usage_redistribution(...) or run with --test.")


if __name__ == "__main__":
    main()

