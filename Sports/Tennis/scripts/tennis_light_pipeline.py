#!/usr/bin/env python3
"""
Light Tennis ETL: step1 PrizePicks CSV -> step7_ranked.xlsx + step8_direction_clean.xlsx.

Produces combined_slate_tickets-compatible workbooks. Direction is placeholder OVER with
projection = line until a full stats / direction pipeline exists.

Run from Tennis/ (or pass absolute paths):
  py -3.14 scripts/tennis_light_pipeline.py
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


def _norm_name(val: object) -> str:
    s = str(val or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_sackmann_rows(root: Path) -> pd.DataFrame:
    data_dir = root / "data" / "sackmann"
    candidates = [
        data_dir / "atp_matches_2026.csv",
        data_dir / "wta_matches_2026.csv",
        data_dir / "atp_matches_combined.csv",
        data_dir / "wta_matches_combined.csv",
    ]
    frames: list[pd.DataFrame] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            frames.append(pd.read_csv(path, low_memory=False))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    src = pd.concat(frames, ignore_index=True)
    need = {"winner_name", "loser_name", "w_ace", "l_ace", "w_df", "l_df"}
    if not need.issubset(src.columns):
        return pd.DataFrame()

    def _side(df: pd.DataFrame, is_winner: bool) -> pd.DataFrame:
        pcol = "winner_name" if is_winner else "loser_name"
        ocol = "loser_name" if is_winner else "winner_name"
        acol = "w_ace" if is_winner else "l_ace"
        dcol = "w_df" if is_winner else "l_df"
        out = pd.DataFrame(
            {
                "player_key": df[pcol].map(_norm_name),
                "opponent_key": df[ocol].map(_norm_name),
                "aces": pd.to_numeric(df[acol], errors="coerce"),
                "double_faults": pd.to_numeric(df[dcol], errors="coerce"),
            }
        )
        out = out[(out["player_key"] != "") & (out["opponent_key"] != "")]
        return out

    merged = pd.concat([_side(src, True), _side(src, False)], ignore_index=True)
    if merged.empty:
        return merged
    merged = (
        merged.groupby(["player_key", "opponent_key"], as_index=False)
        .agg({"aces": "mean", "double_faults": "mean"})
        .fillna(0.0)
    )
    return merged


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Tennis light ETL: step1 -> step7 + step8 xlsx.")
    ap.add_argument("--input", default="outputs/step1_tennis_props.csv")
    ap.add_argument("--step7-out", default="outputs/step7_tennis_ranked.xlsx")
    ap.add_argument("--step8-xlsx", default="outputs/step8_tennis_direction_clean.xlsx")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    if not inp.is_file():
        raise SystemExit(f"Missing input: {inp}")

    raw = pd.read_csv(inp, dtype=str, low_memory=False)
    raw["line"] = pd.to_numeric(raw.get("line"), errors="coerce")
    raw = raw.dropna(subset=["line"])
    raw = raw[raw["line"] >= 0]

    sort_cols = [c for c in ("start_time", "player", "prop_type") if c in raw.columns]
    work = raw.sort_values(sort_cols, na_position="last").reset_index(drop=True)
    n = max(len(work), 1)
    rank_score = 4.0 + 3.0 * (np.arange(n, dtype=float) / max(n - 1, 1))
    work["rank_score"] = rank_score

    def tier_for(rs: float) -> str:
        if rs >= 6.2:
            return "A"
        if rs >= 5.3:
            return "B"
        return "C"

    work["tier"] = work["rank_score"].map(tier_for)
    opp_col = "opp_team" if "opp_team" in work.columns else "opp"

    out = pd.DataFrame(
        {
            "Player": work.get("player", pd.Series([""] * len(work))).fillna("").astype(str).str.strip(),
            "Tier": work["tier"],
            "Rank Score": work["rank_score"],
            "Pos": work.get("pos", pd.Series([""] * len(work))).fillna("").astype(str),
            "Team": work.get("team", pd.Series([""] * len(work))).fillna("").astype(str).str.upper(),
            "Opp": work.get(opp_col, pd.Series([""] * len(work))).fillna("").astype(str).str.upper(),
            "Game Time": work.get("start_time", pd.Series([""] * len(work))).fillna("").astype(str),
            "Prop": work.get("prop_type", pd.Series([""] * len(work))).fillna("").astype(str),
            "Pick Type": work.get("pick_type", pd.Series(["Standard"] * len(work))).fillna("Standard").astype(str),
            "Line": work["line"],
            "Direction": ["OVER"] * len(work),
            "Edge": [0.0] * len(work),
            "Projection": work["line"],
            "Hit Rate (5g)": np.nan,
            "Last 5 Avg": np.nan,
            "Season Avg": np.nan,
            "L5 Over": np.nan,
            "L5 Under": np.nan,
            "L10 Over": np.nan,
            "L10 Under": np.nan,
            "Def Tier": ["LEAGUE AVG"] * len(work),
        }
    )

    # Fill tennis serve props from Sackmann winner/loser stat columns.
    sack = _load_sackmann_rows(root)
    if not sack.empty:
        out["player_key"] = out["Player"].map(_norm_name)
        out["opponent_key"] = out["Opp"].map(_norm_name)
        out = out.merge(sack, how="left", on=["player_key", "opponent_key"])
        out["aces"] = pd.to_numeric(out.get("aces"), errors="coerce").fillna(0.0)
        out["double_faults"] = pd.to_numeric(out.get("double_faults"), errors="coerce").fillna(0.0)
        is_ace = out["Prop"].astype(str).str.lower().str.contains("aces", na=False)
        is_df = out["Prop"].astype(str).str.lower().str.contains("double faults?|double_faults", na=False)
        out.loc[is_ace, "Season Avg"] = out.loc[is_ace, "aces"]
        out.loc[is_df, "Season Avg"] = out.loc[is_df, "double_faults"]
        out = out.drop(columns=["player_key", "opponent_key"], errors="ignore")

    # Refresh match cache from ESPN scoreboard, then enrich aces/DF from Sackmann.
    cache_path = root / "cache" / "tennis_match_games.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    match_cache: dict[str, list[dict[str, object]]] = {}
    try:
        import sys

        scripts_dir = root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from tennis_shared import refresh_match_games_cache  # noqa: WPS433

        match_cache = refresh_match_games_cache(cache_path)
    except Exception:
        if cache_path.is_file():
            try:
                match_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                match_cache = {}
    sack_lookup: dict[tuple[str, str], tuple[float, float]] = {}
    if not sack.empty:
        for r in sack.itertuples(index=False):
            key = (str(getattr(r, "player_key", "")), str(getattr(r, "opponent_key", "")))
            sack_lookup[key] = (float(getattr(r, "aces", 0.0) or 0.0), float(getattr(r, "double_faults", 0.0) or 0.0))
    if match_cache:
        for aid, rows_for_player in match_cache.items():
            if not isinstance(rows_for_player, list):
                continue
            for m in rows_for_player:
                pkey = _norm_name(m.get("player"))
                okey = _norm_name(m.get("opponent"))
                aces_df = sack_lookup.get((pkey, okey))
                if not aces_df:
                    continue
                m["aces"] = aces_df[0]
                m["double_faults"] = aces_df[1]
        cache_path.write_text(json.dumps(match_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    s7 = Path(args.step7_out)
    if not s7.is_absolute():
        s7 = root / s7
    s8 = Path(args.step8_xlsx)
    if not s8.is_absolute():
        s8 = root / s8
    s7.parent.mkdir(parents=True, exist_ok=True)
    s8.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(s7, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL", index=False)

    with pd.ExcelWriter(s8, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="Tennis", index=False)
        out.to_excel(w, sheet_name="ALL", index=False)

    print(f"OK step7 -> {s7}  rows={len(out)}")
    print(f"OK step8 -> {s8}  rows={len(out)}")


if __name__ == "__main__":
    main()
