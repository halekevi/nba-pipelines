#!/usr/bin/env python3
"""
Top-N players per stat category per NBA team, with historical over/under-performance
vs opponent defensive rank (team-level defense from defense_team_summary.csv).

Use for edge / top-edge context: leaders who spike vs weak defenses are natural OVER
candidates when tonight's opp_def_rank is high (weak D).

Run (from repo root):
  py -3 Sports/NBA/scripts/analyze_top_players_vs_defense.py
  py -3 Sports/NBA/scripts/analyze_top_players_vs_defense.py --slate outputs/2026-05-28/nba/step8_all_direction.csv
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_NBA = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# ESPN cache abbrev -> defense CSV / slate abbrev
ESPN_TO_DEF: dict[str, str] = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "PHO": "PHX",
    "WSH": "WAS",
    "UTAH": "UTA",
    "BRK": "BKN",
}

CATEGORIES: tuple[str, ...] = (
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "stocks",
    "fg3m",
    "pra",
)

MIN_GAMES_DEFAULT = 8
TOP_N_DEFAULT = 3
BOTTOM_N_DEFAULT = 3


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def defense_team_key(team: object) -> str:
    s = str(team or "").strip().upper()
    return ESPN_TO_DEF.get(s, s)


def _slate_team(espn_team: str) -> str:
    return defense_team_key(espn_team)


def _load_defense(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, encoding="utf-8-sig")
    d["def_key"] = d["TEAM_ABBREVIATION"].astype(str).str.upper().map(defense_team_key)
    d["OVERALL_DEF_RANK"] = pd.to_numeric(d["OVERALL_DEF_RANK"], errors="coerce")
    return d.drop_duplicates(subset=["def_key"], keep="first")


def _attach_opponents(games: pd.DataFrame) -> pd.DataFrame:
    """Add opp_team (ESPN abbrev) per row from game_id pairing."""
    out = games.copy()
    out["opp_team"] = ""
    for gid, grp in out.groupby("event_id", sort=False):
        teams = grp["TEAM"].astype(str).str.upper().unique().tolist()
        if len(teams) != 2:
            continue
        t0, t1 = teams[0], teams[1]
        mask = out["event_id"] == gid
        out.loc[mask & (out["TEAM"].astype(str).str.upper() == t0), "opp_team"] = t1
        out.loc[mask & (out["TEAM"].astype(str).str.upper() == t1), "opp_team"] = t0
    return out


def _normalize_boxscores(df: pd.DataFrame) -> pd.DataFrame:
    """Map espn_boxscores_cache.csv columns to WNBA-style names."""
    out = df.copy()
    rename = {
        "player": "PLAYER_NAME",
        "team": "TEAM",
        "date": "game_date",
        "game_id": "event_id",
        "points": "PTS",
        "totalRebounds": "REB",
        "assists": "AST",
        "steals": "STL",
        "blocks": "BLK",
        "threePointFieldGoalsMade": "FG3M",
        "freeThrowsMade": "FTM",
    }
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out[dst] = out[src]
    out["PLAYER_NORM"] = out["PLAYER_NAME"].map(_norm_name)
    out["TEAM"] = out["TEAM"].astype(str).str.upper()
    for col in ("PTS", "REB", "AST", "STL", "BLK", "FG3M", "FTM"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _game_logs(cache: Path, season: str | None) -> pd.DataFrame:
    df = pd.read_csv(cache, low_memory=False, encoding="utf-8-sig")
    if df.empty:
        return df
    df = _normalize_boxscores(df)
    if season and "date" in df.columns:
        df["_season_year"] = pd.to_datetime(df["game_date"], errors="coerce").dt.year
        yr = int(season[:4]) if len(season) >= 4 else int(season)
        df = df[df["_season_year"] == yr]
    df = df.sort_values(["PLAYER_NORM", "game_date", "event_id"])
    df = _attach_opponents(df)
    return df[df["opp_team"].astype(str).str.len() > 0].copy()


def _derive_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    pts = pd.to_numeric(df.get("PTS"), errors="coerce")
    reb = pd.to_numeric(df.get("REB"), errors="coerce")
    ast = pd.to_numeric(df.get("AST"), errors="coerce")
    stl = pd.to_numeric(df.get("STL"), errors="coerce")
    blk = pd.to_numeric(df.get("BLK"), errors="coerce")
    fg3m = pd.to_numeric(df.get("FG3M"), errors="coerce")
    if cat == "pts":
        return pts
    if cat == "reb":
        return reb
    if cat == "ast":
        return ast
    if cat == "stl":
        return stl
    if cat == "blk":
        return blk
    if cat == "fg3m":
        return fg3m
    if cat == "stocks":
        return stl.fillna(0) + blk.fillna(0)
    if cat == "pra":
        return pts.fillna(0) + reb.fillna(0) + ast.fillna(0)
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
                        "actual": vals.loc[idx],
                        "delta": vals.loc[idx] - base if pd.notna(base) and pd.notna(vals.loc[idx]) else np.nan,
                    }
                )
    wide = pd.DataFrame(rows)
    if wide.empty:
        return logs
    pivot = wide.pivot_table(index="idx", columns="category", values="delta", aggfunc="first")
    pivot.columns = [f"delta_{c}" for c in pivot.columns]
    base = wide.pivot_table(index="idx", columns="category", values="baseline", aggfunc="first")
    base.columns = [f"baseline_{c}" for c in base.columns]
    return logs.join(pivot, how="left").join(base, how="left")


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
    n_teams = int(defense["OVERALL_DEF_RANK"].notna().sum()) or 30
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
        lambda k: def_by_key.loc[k, "DEF_TIER"] if k in def_by_key.index else ""
    )
    logs["def_bucket"] = logs["OVERALL_DEF_RANK"].map(lambda r: _def_buckets(r, n_teams))

    season_avgs: list[pd.DataFrame] = []
    for cat in CATEGORIES:
        col = f"stat_{cat}"
        agg = (
            logs.groupby(["PLAYER_NAME", "PLAYER_NORM", "TEAM"], as_index=False)
            .agg(season_avg=(col, "mean"), games=(col, "count"))
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
    out["overperform_vs_weak"] = (
        (out["def_boost"] > 0.5)
        & (out["n_weak"].fillna(0) >= 2)
        & (out["weak_over_rate"].fillna(0) >= 0.55)
    )
    out["fades_vs_elite"] = (
        (out["avg_delta_vs_elite"].fillna(0) < -0.3)
        & (out["n_elite"].fillna(0) >= 2)
    )
    out["PLAYER_NORM"] = out["PLAYER_NORM"].astype(str)
    if "leader_side" not in out.columns:
        out["leader_side"] = "top"
    return out.sort_values(["team_slate", "category", "leader_side", "rank_on_team"])


def _dedupe_slate(slate: pd.DataFrame) -> pd.DataFrame:
    slate = slate.copy()
    slate["player_norm"] = slate.get("player", "").map(_norm_name)
    if "prop_norm" not in slate.columns and "prop_type" in slate.columns:
        slate["prop_norm"] = slate["prop_type"].astype(str).str.lower()
    slate["prop_norm"] = slate["prop_norm"].astype(str).str.lower().str.strip()
    slate["_rs"] = pd.to_numeric(slate.get("rank_score"), errors="coerce")
    slate["_ed"] = pd.to_numeric(slate.get("edge"), errors="coerce")
    slate = slate.sort_values(["_rs", "_ed"], ascending=[False, False], na_position="last")
    return slate.drop_duplicates(subset=["player_norm", "prop_norm"], keep="first")


def n_teams_from_slate(df: pd.DataFrame) -> int:
    ranks = pd.to_numeric(df.get("slate_opp_def_rank"), errors="coerce").dropna()
    if ranks.empty:
        return 30
    return max(int(ranks.max()), 30)


def attach_slate(
    leaders: pd.DataFrame,
    slate_path: Path,
    *,
    min_rank_score: float | None,
    min_edge: float | None,
) -> pd.DataFrame:
    if leaders.empty or not slate_path.exists():
        return leaders

    if slate_path.suffix.lower() in (".xlsx", ".xls"):
        slate = pd.read_excel(slate_path, dtype=str)
    else:
        slate = pd.read_csv(slate_path, dtype=str, encoding="utf-8-sig")

    slate = _dedupe_slate(slate)
    pick_cols = [
        c
        for c in (
            "player",
            "team",
            "opp_team",
            "prop_norm",
            "line",
            "bet_direction",
            "final_bet_direction",
            "rank_score",
            "edge",
            "edge_score",
            "tier",
            "OVERALL_DEF_RANK",
            "DEF_TIER",
            "line_hit_rate_over_ou_5",
            "stat_last5_avg",
        )
        if c in slate.columns
    ]
    slate_sub = slate[pick_cols + ["player_norm"]].copy()
    slate_sub = slate_sub.rename(
        columns={
            "OVERALL_DEF_RANK": "slate_opp_def_rank",
            "DEF_TIER": "slate_opp_def_tier",
        }
    )

    merged = leaders.merge(
        slate_sub,
        left_on=["PLAYER_NORM", "category"],
        right_on=["player_norm", "prop_norm"],
        how="left",
    )
    merged["on_slate"] = merged["player"].notna()
    merged["slate_edge_signal"] = False

    rs = pd.to_numeric(merged.get("rank_score"), errors="coerce")
    ed = pd.to_numeric(merged.get("edge"), errors="coerce")
    opp_rank = pd.to_numeric(merged.get("slate_opp_def_rank"), errors="coerce")
    direction = merged.get("final_bet_direction", merged.get("bet_direction", "")).astype(str).str.upper()
    boost = merged["overperform_vs_weak"].fillna(False)
    weak_cut = int(np.ceil(n_teams_from_slate(merged) * 0.65)) if opp_rank.notna().any() else 20
    weak_opp = opp_rank >= weak_cut
    tier = merged.get("slate_opp_def_tier", pd.Series("", index=merged.index)).astype(str)
    weak_tier = tier.isin(["Weak", "Below Avg"])
    merged["slate_edge_signal"] = (
        boost & merged["on_slate"] & direction.eq("OVER") & (weak_opp.fillna(False) | weak_tier)
    )
    if min_rank_score is not None:
        merged.loc[rs < min_rank_score, "slate_edge_signal"] = False
    if min_edge is not None:
        merged.loc[ed < min_edge, "slate_edge_signal"] = False
    return merged


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
    if "slate_edge_signal" in df.columns:
        sig = df[df["slate_edge_signal"].fillna(False)]
        print(f"Slate OVER + weak opp + def boost: {len(sig)}")
        if not sig.empty:
            show = sig.sort_values(
                ["rank_score", "edge"],
                ascending=[False, False],
                key=lambda c: pd.to_numeric(c, errors="coerce"),
            )
            cols = [
                c
                for c in (
                    "player",
                    "team",
                    "category",
                    "rank_on_team",
                    "opp_team",
                    "slate_opp_def_rank",
                    "def_boost",
                    "edge",
                    "rank_score",
                    "tier",
                )
                if c in show.columns
            ]
            print("\n--- Top slate edge signals ---")
            print(show[cols].head(20).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="NBA top-N per team vs opponent defense ranks")
    ap.add_argument("--cache", default=str(_NBA / "data" / "cache" / "espn_boxscores_cache.csv"))
    ap.add_argument("--defense", default=str(_NBA / "data" / "cache" / "defense_team_summary.csv"))
    ap.add_argument("--season", default="", help="Filter to calendar year of game date (e.g. 2025)")
    ap.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    ap.add_argument("--min-games", type=int, default=MIN_GAMES_DEFAULT)
    ap.add_argument("--slate", default="", help="step8 CSV/XLSX to overlay tonight's props")
    ap.add_argument("--out", default=str(_NBA / "data" / "nba_top3_vs_defense.csv"))
    ap.add_argument("--min-rank-score", type=float, default=None)
    ap.add_argument("--min-edge", type=float, default=None)
    args = ap.parse_args()

    cache = Path(args.cache)
    defense = _load_defense(Path(args.defense))
    logs = _game_logs(cache, args.season or None)
    if logs.empty:
        print(f"No game logs in {cache}")
        return

    if not args.season and "game_date" in logs.columns:
        latest = pd.to_datetime(logs["game_date"], errors="coerce").max()
        if pd.notna(latest):
            yr = int(latest.year)
            logs = logs[pd.to_datetime(logs["game_date"], errors="coerce").dt.year == yr]
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

    slate_path = Path(args.slate) if args.slate else Path(_NBA / "data" / "outputs" / "step8_all_direction.csv")
    if slate_path.exists():
        slate_view = attach_slate(
            result,
            slate_path,
            min_rank_score=args.min_rank_score,
            min_edge=args.min_edge,
        )
        slate_out = out.with_name(out.stem + "_slate" + out.suffix)
        slate_view.to_csv(slate_out, index=False, encoding="utf-8-sig")
        print(f"Wrote {slate_out} ({len(slate_view)} rows)")
        _print_summary(slate_view)
    else:
        if args.slate:
            print(f"Warning: slate not found: {slate_path}")
        _print_summary(result)


if __name__ == "__main__":
    main()
