#!/usr/bin/env python3
"""
Top-N hitters per stat category per MLB team, with historical over/under-performance
vs opponent pitching rank (team-level composite from mlb_defense_summary.csv).

Hitter props only: hits, total_bases, home_runs, rbi. Pitcher K/outs/ER use a separate
starter-centric model (mlb_pitcher_matchup.csv — deferred).

Data source: mlb_stats_cache.csv (MLB Stats API game logs via step4). Rows without
TEAM_ID/OPP_TEAM_ID are skipped; skipped count is logged for backfill decisions.

Defense interpretation: OVERALL_DEF_RANK is opposing *pitching* rank (rank 1 = elite
pitching, hard for hitters). Weak pitching (high rank) = favorable for OVER on hitters.

Run (from repo root):
  py -3 Sports/MLB/scripts/analyze_top_hitters_vs_defense.py
  py -3 Sports/MLB/scripts/analyze_top_hitters_vs_defense.py --season 2025
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_MLB = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Stats API teamId -> slate/defense abbrev (mirrors step4_attach_player_stats_mlb.py)
MLB_TEAM_ID_MAP: dict[str, int] = {
    "ARI": 109,
    "AZ": 109,
    "ATL": 144,
    "BAL": 110,
    "BOS": 111,
    "CHC": 112,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "CWS": 145,
    "CHW": 145,
    "DET": 116,
    "HOU": 117,
    "KC": 118,
    "KCR": 118,
    "LAA": 108,
    "LAD": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYM": 121,
    "NYY": 147,
    "ATH": 133,
    "OAK": 133,
    "PHI": 143,
    "PIT": 134,
    "SD": 135,
    "SDP": 135,
    "SF": 137,
    "SFG": 137,
    "SEA": 136,
    "STL": 138,
    "TB": 139,
    "TBR": 139,
    "TEX": 140,
    "TOR": 141,
    "WSH": 120,
    "WSN": 120,
    "WAS": 120,
}

CATEGORIES: tuple[str, ...] = ("hits", "total_bases", "home_runs", "rbi")

SLATE_STAT_ALIASES: dict[str, str] = {
    "total bases": "total_bases",
    "tb": "total_bases",
    "hr": "home_runs",
    "home runs": "home_runs",
}

MIN_GAMES_DEFAULT = 10
TOP_N_DEFAULT = 3
BOTTOM_N_DEFAULT = 3

# MLB counting stats are sparse — scaled below NBA/WNBA thresholds
_BOOST_THRESH: dict[str, float] = {
    "hits": 0.20,
    "total_bases": 0.30,
    "home_runs": 0.10,
    "rbi": 0.15,
}
_FADE_THRESH: dict[str, float] = {
    "hits": -0.10,
    "total_bases": -0.15,
    "home_runs": -0.05,
    "rbi": -0.08,
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _title_name(norm: str) -> str:
    return " ".join(w.capitalize() for w in str(norm or "").split())


def defense_team_key(team: object) -> str:
    s = str(team or "").strip().upper()
    aliases = {"AZ": "ARI", "CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF", "TBR": "TB", "WSN": "WSH", "WAS": "WSH", "ATH": "ATH"}
    return aliases.get(s, s)


def _slate_team(abbr: str) -> str:
    return defense_team_key(abbr)


def _id_to_abbrev() -> dict[str, str]:
    """Prefer defense-CSV-style abbrevs (first key per team id wins)."""
    out: dict[str, str] = {}
    for abbr, tid in MLB_TEAM_ID_MAP.items():
        key = str(int(tid))
        if key not in out:
            out[key] = defense_team_key(abbr)
    return out


def _norm_team_id(val: object) -> str:
    s = str(val or "").strip()
    if not s or s.lower() == "nan":
        return ""
    try:
        return str(int(float(s)))
    except (TypeError, ValueError):
        return ""


def _load_defense(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, encoding="utf-8-sig")
    d["def_key"] = d["TEAM_ABBREVIATION"].astype(str).str.upper().map(defense_team_key)
    d["OVERALL_DEF_RANK"] = pd.to_numeric(d["OVERALL_DEF_RANK"], errors="coerce")
    tier = d.get("DEF_TIER", d.get("def_tier", ""))
    d["def_tier"] = tier.astype(str)
    return d.drop_duplicates(subset=["def_key"], keep="first")


def _load_player_names(id_cache: Path, slate_paths: list[Path]) -> dict[str, str]:
    names: dict[str, str] = {}
    if id_cache.is_file():
        ic = pd.read_csv(id_cache, encoding="utf-8-sig")
        for _, r in ic.iterrows():
            pid = str(r.get("mlb_player_id", "")).strip()
            norm = str(r.get("player_norm", "")).strip()
            if pid and norm and pid not in names:
                names[pid] = _title_name(norm)
    for sp in slate_paths:
        if not sp.is_file():
            continue
        try:
            sl = pd.read_csv(sp, encoding="utf-8-sig", low_memory=False)
        except Exception:
            continue
        pid_col = next((c for c in ("mlb_player_id", "MLB_PLAYER_ID") if c in sl.columns), None)
        if not pid_col:
            continue
        name_col = next((c for c in ("player_name", "player", "PLAYER_NAME") if c in sl.columns), None)
        if not name_col:
            continue
        for _, r in sl[[pid_col, name_col]].drop_duplicates().iterrows():
            pid = str(r[pid_col]).strip()
            nm = str(r[name_col]).strip()
            if pid and nm and pid != "nan":
                names[pid] = nm
    return names


def _load_game_logs(cache_path: Path, season: str | None) -> tuple[pd.DataFrame, dict[str, int]]:
    stats: dict[str, int] = {"rows_in": 0, "rows_skipped_no_ids": 0, "wide_games": 0}
    if not cache_path.is_file():
        raise FileNotFoundError(f"Missing cache: {cache_path}")

    df = pd.read_csv(cache_path, low_memory=False, encoding="utf-8-sig")
    df = df[df["PLAYER_TYPE"].astype(str).str.lower().eq("hitter")].copy()
    df = df[df["PROP_NORM"].astype(str).isin(CATEGORIES)].copy()
    stats["rows_in"] = len(df)
    if df.empty:
        return df, stats

    if season:
        df = df[df["SEASON"].astype(str) == str(season)].copy()

    df["TEAM_ID_N"] = df["TEAM_ID"].map(_norm_team_id)
    df["OPP_TEAM_ID_N"] = df["OPP_TEAM_ID"].map(_norm_team_id)
    missing = df["TEAM_ID_N"].eq("") | df["OPP_TEAM_ID_N"].eq("")
    stats["rows_skipped_no_ids"] = int(missing.sum())
    df = df[~missing].copy()
    if df.empty:
        return df, stats

    id_map = _id_to_abbrev()
    wide = df.pivot_table(
        index=["MLB_PLAYER_ID", "GAME_ID", "GAME_DATE", "TEAM_ID_N", "OPP_TEAM_ID_N", "SEASON"],
        columns="PROP_NORM",
        values="STAT_VALUE",
        aggfunc="first",
    ).reset_index()
    for cat in CATEGORIES:
        if cat in wide.columns:
            wide[cat] = pd.to_numeric(wide[cat], errors="coerce")
        else:
            wide[cat] = np.nan
    wide = wide.dropna(subset=list(CATEGORIES), how="any")
    stats["wide_games"] = len(wide)

    wide["TEAM"] = wide["TEAM_ID_N"].map(id_map)
    wide["opp_team"] = wide["OPP_TEAM_ID_N"].map(id_map)
    wide = wide[wide["TEAM"].notna() & wide["opp_team"].notna()].copy()
    wide["game_date"] = pd.to_datetime(wide["GAME_DATE"], errors="coerce")
    wide["MLB_PLAYER_ID"] = wide["MLB_PLAYER_ID"].astype(str)
    return wide, stats


def _attach_player_names(logs: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    out = logs.copy()
    out["PLAYER_NAME"] = out["MLB_PLAYER_ID"].map(names).fillna("")
    blank = out["PLAYER_NAME"].astype(str).str.len() == 0
    out.loc[blank, "PLAYER_NAME"] = out.loc[blank, "MLB_PLAYER_ID"].map(lambda x: f"Player {x}")
    out["PLAYER_NORM"] = out["PLAYER_NAME"].map(_norm_name)
    return out


def _derive_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    if cat in df.columns:
        return pd.to_numeric(df[cat], errors="coerce")
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
        lambda k: def_by_key.loc[k, "def_tier"] if k in def_by_key.index else ""
    )
    logs["def_bucket"] = logs["OVERALL_DEF_RANK"].map(lambda r: _def_buckets(r, n_teams))

    season_avgs: list[pd.DataFrame] = []
    for cat in CATEGORIES:
        col = f"stat_{cat}"
        agg = (
            logs.groupby(["PLAYER_NAME", "PLAYER_NORM", "TEAM"], as_index=False)
            .agg(season_avg=(col, "mean"), games=(col, "count"))
        )
        agg["avg_toi"] = np.nan
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
        thresh = _BOOST_THRESH.get(cat, 0.25)
        return (
            pd.notna(boost)
            and float(boost) > thresh
            and float(row.get("n_weak") or 0) >= 2
            and float(row.get("weak_over_rate") or 0) >= 0.55
        )

    def _fade(row) -> bool:
        cat = str(row["category"])
        elite_d = row.get("avg_delta_vs_elite")
        thresh = _FADE_THRESH.get(cat, -0.10)
        return (
            pd.notna(elite_d)
            and float(elite_d) < thresh
            and float(row.get("n_elite") or 0) >= 2
        )

    out["overperform_vs_weak"] = out.apply(_overperform, axis=1)
    out["fades_vs_elite"] = out.apply(_fade, axis=1)
    out["PLAYER_NORM"] = out["PLAYER_NORM"].astype(str)
    return out.sort_values(["team_slate", "category", "leader_side", "rank_on_team"])


def _pick_default_season(logs: pd.DataFrame, min_games: int) -> str:
    """Prefer the season with the broadest team coverage (not just most game rows)."""
    best_season = str(logs["SEASON"].astype(str).iloc[0])
    best_teams = -1
    for season, grp in logs.groupby("SEASON", sort=False):
        per_team = grp.groupby(["TEAM", "MLB_PLAYER_ID"]).size()
        teams_with_sample = per_team.groupby(level=0).apply(lambda s: (s >= min_games).any()).sum()
        if int(teams_with_sample) > best_teams:
            best_teams = int(teams_with_sample)
            best_season = str(season)
    return best_season


def _print_summary(df: pd.DataFrame, stats: dict[str, int], season: str) -> None:
    print(
        f"Cache: {stats['rows_in']} hitter stat rows | skipped (no team ids): {stats['rows_skipped_no_ids']} | "
        f"wide games: {stats['wide_games']} | season: {season}"
    )
    if df.empty:
        print("No leader rows produced.")
        return
    print(
        f"Teams: {df['team_slate'].nunique()} | Categories: {df['category'].nunique()} | "
        f"Leader rows: {len(df)}"
    )
    boosters = df[df["overperform_vs_weak"].fillna(False)]
    print(f"Historical weak-pitching boosters (top-{TOP_N_DEFAULT} per team×cat): {len(boosters)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="MLB top-N hitters per team vs opponent pitching ranks")
    ap.add_argument("--cache", default=str(_MLB / "mlb_stats_cache.csv"))
    ap.add_argument("--defense", default=str(_MLB / "mlb_defense_summary.csv"))
    ap.add_argument("--id-cache", default=str(_MLB / "mlb_id_cache.csv"))
    ap.add_argument("--season", default=None, help="Season year (e.g. 2025); default = latest in cache")
    ap.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    ap.add_argument("--min-games", type=int, default=MIN_GAMES_DEFAULT)
    ap.add_argument("--out", default=str(_MLB / "data" / "mlb_hitter_top3_vs_defense.csv"))
    ap.add_argument("--slate", default=None, help="Optional step8 slate for player display names")
    args = ap.parse_args()

    slate_paths = [Path(args.slate)] if args.slate else [
        _MLB / "step8_mlb_direction.csv",
        _MLB / "outputs" / "step8_mlb_direction.csv",
    ]
    names = _load_player_names(Path(args.id_cache), slate_paths)

    logs, stats = _load_game_logs(Path(args.cache), args.season)
    if logs.empty:
        print("No hitter game logs with populated team ids")
        _print_summary(pd.DataFrame(), stats, str(args.season or ""))
        return

    season = str(args.season) if args.season else _pick_default_season(logs, args.min_games)
    logs = logs[logs["SEASON"].astype(str) == season].copy()
    print(f"Using season {season} ({len(logs)} game rows)")

    logs = _attach_player_names(logs, names)
    defense = _load_defense(Path(args.defense))

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
    _print_summary(result, stats, season)


if __name__ == "__main__":
    main()
