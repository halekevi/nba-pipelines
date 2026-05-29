#!/usr/bin/env python3
"""
Top-N skaters per stat category per NHL team, with historical over/under-performance
vs opponent defensive rank (team-level GAA composite from nhl_defense_summary.csv).

Skater props only: goals, assists, points, shots. Goalie saves use a separate model.

Data source: proporacle_ref.db `nhl` table (ESPN boxscores via build_boxscore_ref.py).

Run (from repo root):
  py -3 Sports/NHL/scripts/analyze_top_players_vs_defense.py
  py -3 Sports/NHL/scripts/analyze_top_players_vs_defense.py --slate Sports/NHL/step8_nhl_direction_clean.csv
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_NHL = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# ESPN / DB abbrev -> defense CSV team column
SLATE_TO_DEF: dict[str, str] = {
    "LA": "LAK",
    "NJ": "NJD",
    "SJ": "SJS",
    "TB": "TBL",
    "CLB": "CBJ",
    "ARZ": "UTA",
}

CATEGORIES: tuple[str, ...] = ("goals", "assists", "points", "shots")

# stat_norm aliases on the slate → category id in this CSV
SLATE_STAT_ALIASES: dict[str, str] = {
    "shots_on_goal": "shots",
    "sog": "shots",
}

MIN_GAMES_DEFAULT = 10
MIN_TOI_DEFAULT = 8.0
TOP_N_DEFAULT = 3
BOTTOM_N_DEFAULT = 3

# NHL counting stats have smaller game-to-game deltas than NBA/WNBA
_BOOST_THRESH: dict[str, float] = {
    "goals": 0.20,
    "assists": 0.30,
    "points": 0.40,
    "shots": 0.60,
}
_FADE_THRESH: dict[str, float] = {
    "goals": -0.10,
    "assists": -0.15,
    "points": -0.20,
    "shots": -0.30,
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def defense_team_key(team: object) -> str:
    s = str(team or "").strip().upper()
    return SLATE_TO_DEF.get(s, s)


def _slate_team(abbr: str) -> str:
    return defense_team_key(abbr)


def _load_defense(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, encoding="utf-8-sig")
    d["def_key"] = d["team"].astype(str).str.upper().map(defense_team_key)
    d["OVERALL_DEF_RANK"] = pd.to_numeric(d["OVERALL_DEF_RANK"], errors="coerce")
    return d.drop_duplicates(subset=["def_key"], keep="first")


def _default_db_path() -> Path:
    return _REPO / "data" / "cache" / "proporacle_ref.db"


def _load_game_logs(db_path: Path, season_year: int | None) -> pd.DataFrame:
    if not db_path.is_file():
        raise FileNotFoundError(f"Missing DB: {db_path}\nRun: py scripts/build_boxscore_ref.py --sports nhl --backfill --days 60")
    con = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT player, team, game_date, home_team, away_team, position,
               goals, assists, points, shots_on_goal, toi, saves
        FROM nhl
        ORDER BY game_date, event_id
        """,
        con,
    )
    con.close()
    if df.empty:
        return df

    df["PLAYER_NAME"] = df["player"].astype(str)
    df["PLAYER_NORM"] = df["PLAYER_NAME"].map(_norm_name)
    df["TEAM"] = df["team"].astype(str).str.upper()
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    if season_year is not None:
        df = df[df["game_date"].dt.year == season_year]

    # Skaters only for team scoring-leader ranks
    pos = df["position"].astype(str).str.upper()
    df = df[~pos.eq("G")].copy()
    df["toi"] = pd.to_numeric(df["toi"], errors="coerce")
    df = df[df["toi"].fillna(0) >= MIN_TOI_DEFAULT * 0.5]

    def _opp(row: pd.Series) -> str:
        t, h, a = row["TEAM"], str(row["home_team"]).upper(), str(row["away_team"]).upper()
        if t == h:
            return a
        if t == a:
            return h
        return ""

    df["opp_team"] = df.apply(_opp, axis=1)
    return df[df["opp_team"].astype(str).str.len() > 0].copy()


def _derive_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    if cat == "goals":
        return pd.to_numeric(df["goals"], errors="coerce")
    if cat == "assists":
        return pd.to_numeric(df["assists"], errors="coerce")
    if cat == "points":
        return pd.to_numeric(df["points"], errors="coerce")
    if cat == "shots":
        return pd.to_numeric(df["shots_on_goal"], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _add_stat_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for cat in CATEGORIES:
        out[f"stat_{cat}"] = _derive_stat(out, cat)
    return out


def _player_baselines(logs: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for cat in CATEGORIES:
        col = f"stat_{cat}"
        for _pnorm, grp in logs.groupby("PLAYER_NORM", sort=False):
            g = grp.sort_values("game_date")
            vals = pd.to_numeric(g[col], errors="coerce")
            baseline = vals.expanding(min_periods=3).mean().shift(1)
            for idx, base in zip(g.index, baseline):
                rows.append(
                    {
                        "idx": idx,
                        "category": cat,
                        "baseline": base,
                        "delta": vals.loc[idx] - base if pd.notna(base) and pd.notna(vals.loc[idx]) else np.nan,
                    }
                )
    wide = pd.DataFrame(rows)
    if wide.empty:
        return logs
    pivot = wide.pivot_table(index="idx", columns="category", values="delta", aggfunc="first")
    pivot.columns = [f"delta_{c}" for c in pivot.columns]
    return logs.join(pivot, how="left")


def _def_buckets(rank: float, n_teams: int) -> str:
    if pd.isna(rank):
        return "unknown"
    r = int(rank)
    elite_cut = max(1, int(np.ceil(n_teams * 0.25)))
    weak_cut = max(elite_cut + 1, int(np.floor(n_teams * 0.75)))
    if r <= elite_cut:
        return "elite"
    if r >= weak_cut:
        return "weak"
    return "mid"


def build_leaderboard_and_splits(
    logs: pd.DataFrame,
    defense: pd.DataFrame,
    *,
    top_n: int,
    min_games: int,
) -> pd.DataFrame:
    n_teams = int(defense["OVERALL_DEF_RANK"].notna().sum()) or 32
    def_by_key = defense.set_index("def_key")
    valid_teams = set(def_by_key.index)

    logs = _add_stat_columns(logs.copy())
    logs["team_def_key"] = logs["TEAM"].map(defense_team_key)
    logs = logs[logs["team_def_key"].isin(valid_teams)].copy()
    logs["opp_def_key"] = logs["opp_team"].map(defense_team_key)
    logs["OVERALL_DEF_RANK"] = logs["opp_def_key"].map(
        lambda k: def_by_key.loc[k, "OVERALL_DEF_RANK"] if k in def_by_key.index else np.nan
    )
    logs["DEF_TIER"] = logs["opp_def_key"].map(
        lambda k: def_by_key.loc[k, "def_tier"] if k in def_by_key.index else ""
    )
    logs["def_bucket"] = logs["OVERALL_DEF_RANK"].map(lambda r: _def_buckets(r, n_teams))

    season_avgs: list[pd.DataFrame] = []
    for cat in CATEGORIES:
        col = f"stat_{cat}"
        agg = (
            logs.groupby(["PLAYER_NAME", "PLAYER_NORM", "TEAM"], as_index=False)
            .agg(season_avg=(col, "mean"), games=(col, "count"), avg_toi=("toi", "mean"))
        )
        agg["category"] = cat
        season_avgs.append(agg)
    avgs = pd.concat(season_avgs, ignore_index=True)
    avgs = avgs[avgs["games"] >= min_games]
    avgs["team_slate"] = avgs["TEAM"].map(_slate_team)
    avgs["team_def_key"] = avgs["TEAM"].map(defense_team_key)

    top_rows: list[pd.DataFrame] = []
    bottom_rows: list[pd.DataFrame] = []
    for (_team_key, cat), grp in avgs.groupby(["team_def_key", "category"], sort=False):
        top = grp.nlargest(top_n, "season_avg").copy()
        top["rank_on_team"] = range(1, len(top) + 1)
        top["leader_side"] = "top"
        top_rows.append(top)
        bottom_n = min(BOTTOM_N_DEFAULT, len(grp))
        if bottom_n > 0:
            bottom = grp.nsmallest(bottom_n, "season_avg").copy()
            bottom["rank_on_team"] = range(1, len(bottom) + 1)
            bottom["leader_side"] = "bottom"
            bottom_rows.append(bottom)
    leaders = pd.concat(top_rows + bottom_rows, ignore_index=True) if top_rows else pd.DataFrame()

    split_rows: list[dict] = []
    logs_with_delta = _player_baselines(logs)
    for cat in CATEGORIES:
        dcol = f"delta_{cat}"
        if dcol not in logs_with_delta.columns:
            continue
        sub = logs_with_delta[
            [
                "PLAYER_NORM",
                "PLAYER_NAME",
                "TEAM",
                "game_date",
                "opp_team",
                "opp_def_key",
                "OVERALL_DEF_RANK",
                "DEF_TIER",
                "def_bucket",
                dcol,
            ]
        ].copy()
        sub = sub.rename(columns={dcol: "delta"})
        sub["category"] = cat
        sub = sub.dropna(subset=["delta"])
        for (pnorm, team), g in sub.groupby(["PLAYER_NORM", "TEAM"], sort=False):
            elite = g[g["def_bucket"] == "elite"]["delta"]
            weak = g[g["def_bucket"] == "weak"]["delta"]
            mid = g[g["def_bucket"] == "mid"]["delta"]
            all_d = g["delta"]
            rank_corr = np.nan
            if len(g) >= 5 and g["OVERALL_DEF_RANK"].notna().sum() >= 5:
                rank_corr = float(g[["OVERALL_DEF_RANK", "delta"]].corr().iloc[0, 1])
            split_rows.append(
                {
                    "PLAYER_NORM": pnorm,
                    "PLAYER_NAME": g["PLAYER_NAME"].iloc[0],
                    "TEAM": team,
                    "category": cat,
                    "n_games": len(g),
                    "n_elite": len(elite),
                    "n_weak": len(weak),
                    "avg_delta_all": float(all_d.mean()) if len(all_d) else np.nan,
                    "avg_delta_vs_elite": float(elite.mean()) if len(elite) else np.nan,
                    "avg_delta_vs_weak": float(weak.mean()) if len(weak) else np.nan,
                    "avg_delta_vs_mid": float(mid.mean()) if len(mid) else np.nan,
                    "def_rank_corr": rank_corr,
                    "weak_over_rate": float((weak > 0).mean()) if len(weak) else np.nan,
                    "elite_over_rate": float((elite > 0).mean()) if len(elite) else np.nan,
                }
            )

    splits = pd.DataFrame(split_rows)
    if leaders.empty or splits.empty:
        return leaders

    out = leaders.merge(
        splits,
        on=["PLAYER_NORM", "TEAM", "category"],
        how="left",
        suffixes=("", "_split"),
    )
    out["def_boost"] = out["avg_delta_vs_weak"] - out["avg_delta_vs_elite"]

    def _overperform(row) -> bool:
        cat = str(row["category"])
        boost = row.get("def_boost")
        thresh = _BOOST_THRESH.get(cat, 0.5)
        return (
            pd.notna(boost)
            and float(boost) > thresh
            and float(row.get("n_weak") or 0) >= 2
            and float(row.get("weak_over_rate") or 0) >= 0.55
        )

    def _fade(row) -> bool:
        cat = str(row["category"])
        elite_d = row.get("avg_delta_vs_elite")
        thresh = _FADE_THRESH.get(cat, -0.3)
        return (
            pd.notna(elite_d)
            and float(elite_d) < thresh
            and float(row.get("n_elite") or 0) >= 2
        )

    out["overperform_vs_weak"] = out.apply(_overperform, axis=1)
    out["fades_vs_elite"] = out.apply(_fade, axis=1)
    out["PLAYER_NORM"] = out["PLAYER_NORM"].astype(str)
    return out.sort_values(["team_slate", "category", "leader_side", "rank_on_team"])


def _print_summary(df: pd.DataFrame) -> None:
    if df.empty:
        print("No rows produced.")
        return
    print(
        f"Teams: {df['team_slate'].nunique()} | Categories: {df['category'].nunique()} | "
        f"Leader rows: {len(df)}"
    )
    boosters = df[df["overperform_vs_weak"].fillna(False)]
    print(f"Historical weak-D boosters (top-{TOP_N_DEFAULT} per team×cat): {len(boosters)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="NHL top-N skaters per team vs opponent defense ranks")
    ap.add_argument("--db", default=str(_default_db_path()))
    ap.add_argument("--defense", default=str(_NHL / "cache" / "nhl_defense_summary.csv"))
    ap.add_argument("--season", type=int, default=None, help="Calendar year filter on game_date")
    ap.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    ap.add_argument("--min-games", type=int, default=MIN_GAMES_DEFAULT)
    ap.add_argument("--out", default=str(_NHL / "data" / "nhl_top3_vs_defense.csv"))
    args = ap.parse_args()

    defense = _load_defense(Path(args.defense))
    logs = _load_game_logs(Path(args.db), args.season)
    if logs.empty:
        print("No skater game logs in DB")
        return

    if args.season is None:
        yr = int(logs["game_date"].max().year)
        logs = logs[logs["game_date"].dt.year == yr]
        print(f"Using calendar year {yr} ({len(logs)} game rows)")

    result = build_leaderboard_and_splits(
        logs,
        defense,
        top_n=args.top_n,
        min_games=args.min_games,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(result)} rows)")
    _print_summary(result)


if __name__ == "__main__":
    main()
