#!/usr/bin/env python3
"""
Tennis step4b — Sackmann surface profiles, H2H-on-surface, attach to step4 CSV.

Run after step4_attach_player_stats_tennis.py, before step5.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_TENNIS_ROOT = _SCRIPT_DIR.parent
_REPO = _TENNIS_ROOT.parents[1]
_DATA = _TENNIS_ROOT / "data"
_SACK = _DATA / "sackmann"
_PROFILES = _DATA / "tennis_surface_profiles.json"
_H2H = _DATA / "tennis_h2h_surface.json"
_TOURNAMENTS = _DATA / "tournament_surfaces.json"
_MIN_MATCHES = 5
_PROFILE_MAX_AGE_DAYS = 7

_SURFACE_ENC = {"hard": 0, "clay": 1, "grass": 2}


def _norm_name(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _detect_surface(tournament: str, lookup: dict[str, str]) -> str:
    t = str(tournament or "").strip()
    if not t:
        return lookup.get("default", "Hard")
    for key, surf in lookup.items():
        if key == "default":
            continue
        if key.lower() in t.lower():
            return surf
    return lookup.get("default", "Hard")


def _expand_match_rows(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per player per match (winner + loser perspectives)."""
    m = matches.copy()
    m["surface"] = m["surface"].astype(str).str.strip().str.title() if "surface" in m.columns else "Hard"
    if "tourney_date" in m.columns:
        m["date"] = m["tourney_date"].astype(str)
    elif "date" in m.columns:
        m["date"] = m["date"].astype(str)
    else:
        m["date"] = ""
    w_name = m["winner_name"] if "winner_name" in m.columns else m.get("winner", pd.Series(dtype=str))
    l_name = m["loser_name"] if "loser_name" in m.columns else m.get("loser", pd.Series(dtype=str))
    w = m.assign(
        player_norm=w_name.map(_norm_name),
        player=w_name,
        won=1,
        aces=pd.to_numeric(m["w_ace"], errors="coerce"),
        df=pd.to_numeric(m["w_df"], errors="coerce"),
        svpt=pd.to_numeric(m["w_svpt"], errors="coerce"),
        first_in=pd.to_numeric(m["w_1stIn"], errors="coerce"),
        first_won=pd.to_numeric(m["w_1stWon"], errors="coerce"),
        opp_norm=l_name.map(_norm_name),
    )
    l = m.assign(
        player_norm=l_name.map(_norm_name),
        player=l_name,
        won=0,
        aces=pd.to_numeric(m["l_ace"], errors="coerce"),
        df=pd.to_numeric(m["l_df"], errors="coerce"),
        svpt=pd.to_numeric(m["l_svpt"], errors="coerce"),
        first_in=pd.to_numeric(m["l_1stIn"], errors="coerce"),
        first_won=pd.to_numeric(m["l_1stWon"], errors="coerce"),
        opp_norm=w_name.map(_norm_name),
    )
    cols = ["player_norm", "player", "surface", "won", "aces", "df", "svpt", "first_in", "first_won", "date", "opp_norm"]
    return pd.concat([w[cols], l[cols]], ignore_index=True)


def _build_profiles(matches: pd.DataFrame) -> dict[str, dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y%m%d")
    pm = _expand_match_rows(matches)
    pm = pm[pm["date"].str[:8] >= cutoff[:8]]
    if pm.empty:
        return {}
    pm["first_serve_pct_row"] = pm["first_in"] / pm["svpt"].replace(0, np.nan)
    pm["first_serve_won_row"] = pm["first_won"] / pm["first_in"].replace(0, np.nan)
    profiles: dict[str, dict] = {}
    for (pn, surf), g in pm.groupby(["player_norm", "surface"]):
        n = len(g)
        if n < 1 or not pn:
            continue
        player = str(g["player"].iloc[0])
        key = f"{pn}_{surf}"
        profiles[key] = {
            "player": player,
            "surface": surf,
            "aces_per_match_mean": float(g["aces"].mean(skipna=True)) if g["aces"].notna().any() else None,
            "df_per_match_mean": float(g["df"].mean(skipna=True)) if g["df"].notna().any() else None,
            "first_serve_pct": float(g["first_serve_pct_row"].mean(skipna=True))
            if g["first_serve_pct_row"].notna().any()
            else None,
            "first_serve_won_pct": float(g["first_serve_won_row"].mean(skipna=True))
            if g["first_serve_won_row"].notna().any()
            else None,
            "games_won_per_match": None,
            "games_lost_per_match": None,
            "sets_played_per_match": None,
            "win_rate_on_surface": float(g["won"].mean()),
            "n_matches_on_surface": int(n),
        }
    return profiles


def _build_h2h(matches: pd.DataFrame) -> dict[str, dict]:
    h2h: dict[str, dict] = {}
    for _, m in matches.iterrows():
        surf = str(m.get("surface") or "Hard").strip().title()
        w = _norm_name(m.get("winner_name") or m.get("winner"))
        l = _norm_name(m.get("loser_name") or m.get("loser"))
        if not w or not l:
            continue
        p1, p2 = sorted([w, l])
        key = f"{p1}_{p2}_{surf}"
        rec = h2h.setdefault(
            key,
            {"p1": p1, "p2": p2, "surface": surf, "wins_p1": 0, "wins_p2": 0, "n": 0, "last_winner": "", "last_date": ""},
        )
        rec["n"] += 1
        dt = str(m.get("tourney_date") or m.get("date") or "")
        if w == p1:
            rec["wins_p1"] += 1
        else:
            rec["wins_p2"] += 1
        if dt >= rec.get("last_date", ""):
            rec["last_date"] = dt
            rec["last_winner"] = w
    out: dict[str, dict] = {}
    for key, rec in h2h.items():
        n = rec["n"]
        out[key] = {
            "h2h_win_rate_on_surface": rec["wins_p1"] / n if n else None,
            "n_h2h_matches": n,
            "last_h2h_result": rec["last_winner"],
            "last_h2h_date": rec["last_date"],
        }
    return out


def _ensure_sackmann(*, force_fetch: bool) -> pd.DataFrame:
    combined = _SACK / "atp_matches_combined.csv"
    if force_fetch or not combined.is_file() or (time.time() - combined.stat().st_mtime) / 86400 > _PROFILE_MAX_AGE_DAYS:
        fetch = _SCRIPT_DIR / "fetch_sackmann_data.py"
        if fetch.is_file():
            cmd = [sys.executable, str(fetch)]
            if force_fetch:
                cmd.append("--force")
            subprocess.run(cmd, cwd=str(_REPO), check=False)
    frames = []
    for name in ("atp_matches_combined.csv", "wta_matches_combined.csv"):
        p = _SACK / name
        if p.is_file():
            try:
                frames.append(pd.read_csv(p, low_memory=False))
            except Exception as exc:
                print(f"  [warn] {p}: {exc}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _profile_get(profiles: dict, player: str, surface: str) -> dict | None:
    return profiles.get(f"{_norm_name(player)}_{surface}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--force-fetch", action="store_true")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = (_REPO / inp).resolve()
    out = Path(args.output or args.input)
    if not out.is_absolute():
        out = (_REPO / out).resolve()

    if not inp.is_file():
        print(f"ERROR: missing input {inp}")
        return 1

    tour_lookup = json.loads(_TOURNAMENTS.read_text(encoding="utf-8")) if _TOURNAMENTS.is_file() else {"default": "Hard"}

    matches = _ensure_sackmann(force_fetch=args.force_fetch)
    if matches.empty:
        print("WARN: no Sackmann data — copying input unchanged")
        df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
        df.to_csv(out, index=False, encoding="utf-8-sig")
        return 0

    profiles = _build_profiles(matches)
    h2h = _build_h2h(matches)
    _PROFILES.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    _H2H.write_text(json.dumps(h2h, indent=2), encoding="utf-8")
    print(f"Profiles: {len(profiles)} keys -> {_PROFILES}")
    print(f"H2H surface: {len(h2h)} keys -> {_H2H}")

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    for col in (
        "surface",
        "surface_encoded",
        "aces_per_match_mean",
        "df_per_match_mean",
        "first_serve_pct",
        "first_serve_won_pct",
        "win_rate_on_surface",
        "games_won_per_match",
        "n_matches_on_surface",
        "surface_specialist",
        "surface_struggle",
        "h2h_win_rate_on_surface",
        "n_h2h_matches",
    ):
        if col not in df.columns:
            df[col] = ""

    player_col = "player" if "player" in df.columns else "Player"
    tour_col = next((c for c in ("tournament", "Tournament", "event", "match") if c in df.columns), None)
    opp_col = next((c for c in ("opponent", "opp", "opp_player") if c in df.columns), None)

    for idx, row in df.iterrows():
        player = str(row.get(player_col) or "")
        tournament = str(row.get(tour_col) or "") if tour_col else ""
        surf = _detect_surface(tournament, tour_lookup)
        df.at[idx, "surface"] = surf
        df.at[idx, "surface_encoded"] = str(_SURFACE_ENC.get(surf.lower(), 0))

        prof = _profile_get(profiles, player, surf)
        if prof and int(prof.get("n_matches_on_surface") or 0) >= _MIN_MATCHES:
            for k in (
                "aces_per_match_mean",
                "df_per_match_mean",
                "first_serve_pct",
                "first_serve_won_pct",
                "win_rate_on_surface",
                "games_won_per_match",
                "n_matches_on_surface",
            ):
                v = prof.get(k)
                df.at[idx, k] = "" if v is None else str(v)
            wr = float(prof.get("win_rate_on_surface") or 0)
            n = int(prof.get("n_matches_on_surface") or 0)
            df.at[idx, "surface_specialist"] = str(wr > 0.65 and n >= 15).lower()
            df.at[idx, "surface_struggle"] = str(wr < 0.35 and n >= 10).lower()
        else:
            for k in (
                "aces_per_match_mean",
                "df_per_match_mean",
                "first_serve_pct",
                "first_serve_won_pct",
                "win_rate_on_surface",
                "games_won_per_match",
                "n_matches_on_surface",
                "surface_specialist",
                "surface_struggle",
            ):
                df.at[idx, k] = ""

        if opp_col:
            opp = str(row.get(opp_col) or "")
            p1, p2 = sorted([_norm_name(player), _norm_name(opp)])
            hk = f"{p1}_{p2}_{surf}"
            hrec = h2h.get(hk)
            if hrec and int(hrec.get("n_h2h_matches") or 0) >= 1:
                df.at[idx, "h2h_win_rate_on_surface"] = str(hrec.get("h2h_win_rate_on_surface", ""))
                df.at[idx, "n_h2h_matches"] = str(hrec.get("n_h2h_matches", ""))

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
