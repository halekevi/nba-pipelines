from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SportMatchupConfig:
    sport: str
    display_name: str
    defense_path: Path
    defense_team_col: str
    defense_rank_col: str
    defense_tier_col: str
    defense_name_col: str
    categories: tuple[dict, ...]
    cache_path: Path | None = None
    top3_path: Path | None = None
    slate_glob: str = "slate_sport_{sport}.json"
    min_mpg: float = 12.0
    top_n: int = 5
    elite_rank_cut: int = 4
    opp_metric_label: str = "Opp def rank"
    enabled: bool = True
    matchup_mode: str = "team"  # "player" for tennis (opponent player, not team)
    team_normalize: Callable[[str], str] | None = None


def _basketball_categories() -> tuple[dict, ...]:
    return (
        {"id": "pts", "label": "Points", "threshold": 18.0},
        {"id": "reb", "label": "Rebounds", "threshold": 6.0},
        {"id": "ast", "label": "Assists", "threshold": 4.0},
        {"id": "fg3m", "label": "3-Pointers made", "threshold": 1.5},
        {"id": "stl", "label": "Steals", "threshold": 1.0},
        {"id": "blk", "label": "Blocks", "threshold": 1.0},
        {"id": "pra", "label": "Pts+Reb+Ast", "threshold": 28.0},
    )


def _nba1h_categories() -> tuple[dict, ...]:
    """PP 1H board: points + pts+reb+ast only (no reb/ast/fg3m/stl/blk 1H lines)."""
    return (
        {"id": "pts", "label": "Points (1H)", "threshold": 10.0},
        {"id": "pra", "label": "Pts+Reb+Ast (1H)", "threshold": 16.0},
    )


def _nba1q_categories() -> tuple[dict, ...]:
    """PP 1Q board: points, rebounds, assists (no pra / fg3m / stl / blk 1Q lines)."""
    return (
        {"id": "pts", "label": "Points (1Q)", "threshold": 5.0},
        {"id": "reb", "label": "Rebounds (1Q)", "threshold": 2.0},
        {"id": "ast", "label": "Assists (1Q)", "threshold": 1.5},
    )


def _wnba_team_norm(abbr: str) -> str:
    from utils.wnba_team_keys import defense_team_key

    a = defense_team_key(abbr)
    slate_map = {
        "LV": "LVA", "LA": "LAS", "NY": "NYL", "GS": "GSV", "PHO": "PHX", "CONN": "CON",
    }
    return slate_map.get(a, a)


_NBA_ESPN_TO_DEF = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX",
    "WSH": "WAS", "UTAH": "UTA", "BRK": "BKN",
}


def _nba_team_norm(abbr: str) -> str:
    s = str(abbr or "").strip().upper()
    return _NBA_ESPN_TO_DEF.get(s, s)


_NHL_SLATE_TO_DEF = {
    "LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "CLB": "CBJ", "ARZ": "UTA",
}


def _nhl_team_norm(abbr: str) -> str:
    s = str(abbr or "").strip().upper()
    return _NHL_SLATE_TO_DEF.get(s, s)


SPORT_CONFIGS: dict[str, SportMatchupConfig] = {
    "nba": SportMatchupConfig(
        sport="nba",
        display_name="NBA",
        defense_path=_REPO / "Sports/NBA/data/cache/defense_team_summary.csv",
        defense_team_col="TEAM_ABBREVIATION",
        defense_rank_col="OVERALL_DEF_RANK",
        defense_tier_col="DEF_TIER",
        defense_name_col="TEAM_ABBREVIATION",
        categories=_basketball_categories(),
        cache_path=_REPO / "Sports/NBA/data/cache/espn_boxscores_cache.csv",
        top3_path=_REPO / "Sports/NBA/data/nba_top3_vs_defense.csv",
        min_mpg=10.0,
        team_normalize=_nba_team_norm,
    ),
    "nba1h": SportMatchupConfig(
        sport="nba1h",
        display_name="NBA 1H",
        defense_path=_REPO / "Sports/NBA/data/cache/defense_team_summary.csv",
        defense_team_col="TEAM_ABBREVIATION",
        defense_rank_col="OVERALL_DEF_RANK",
        defense_tier_col="DEF_TIER",
        defense_name_col="TEAM_ABBREVIATION",
        categories=_nba1h_categories(),
        cache_path=_REPO / "Sports/NBA/data/cache/espn_boxscores_cache.csv",
        min_mpg=10.0,
    ),
    "nba1q": SportMatchupConfig(
        sport="nba1q",
        display_name="NBA 1Q",
        defense_path=_REPO / "Sports/NBA/data/cache/defense_team_summary.csv",
        defense_team_col="TEAM_ABBREVIATION",
        defense_rank_col="OVERALL_DEF_RANK",
        defense_tier_col="DEF_TIER",
        defense_name_col="TEAM_ABBREVIATION",
        categories=_nba1q_categories(),
        cache_path=_REPO / "Sports/NBA/data/cache/espn_boxscores_cache.csv",
        min_mpg=10.0,
    ),
    "wnba": SportMatchupConfig(
        sport="wnba",
        display_name="WNBA",
        defense_path=_REPO / "Sports/WNBA/wnba_defense_summary.csv",
        defense_team_col="TEAM_ABBREVIATION",
        defense_rank_col="OVERALL_DEF_RANK",
        defense_tier_col="DEF_TIER",
        defense_name_col="TEAM_NAME",
        categories=(
            {"id": "pts", "label": "Points", "threshold": 15.0},
            {"id": "reb", "label": "Rebounds", "threshold": 6.0},
            {"id": "ast", "label": "Assists", "threshold": 4.0},
            {"id": "fg3m", "label": "3-Pointers made", "threshold": 1.5},
            {"id": "stl", "label": "Steals", "threshold": 1.0},
            {"id": "blk", "label": "Blocks", "threshold": 1.0},
            {"id": "pra", "label": "Pts+Reb+Ast", "threshold": 25.0},
        ),
        cache_path=_REPO / "Sports/WNBA/wnba_espn_cache.csv",
        top3_path=_REPO / "Sports/WNBA/data/wnba_top3_vs_defense.csv",
        min_mpg=14.0,
        team_normalize=_wnba_team_norm,
    ),
    "nhl": SportMatchupConfig(
        sport="nhl",
        display_name="NHL",
        defense_path=_REPO / "Sports/NHL/cache/nhl_defense_summary.csv",
        defense_team_col="team",
        defense_rank_col="def_rank",
        defense_tier_col="def_tier",
        defense_name_col="team",
        categories=(
            {"id": "goals", "label": "Goals", "threshold": 0.4},
            {"id": "assists", "label": "Assists", "threshold": 0.4},
            {"id": "points", "label": "Points", "threshold": 0.8},
            {"id": "shots", "label": "Shots", "threshold": 2.5},
        ),
        cache_path=_REPO / "Sports/NHL/data/nst_player_pp_cache.csv",
        top3_path=_REPO / "Sports/NHL/data/nhl_top3_vs_defense.csv",
        opp_metric_label="Opp def rank (GAA)",
        elite_rank_cut=6,
        team_normalize=_nhl_team_norm,
    ),
    "mlb": SportMatchupConfig(
        sport="mlb",
        display_name="MLB",
        defense_path=_REPO / "Sports/MLB/mlb_defense_summary.csv",
        defense_team_col="TEAM_ABBREVIATION",
        defense_rank_col="OVERALL_DEF_RANK",
        defense_tier_col="DEF_TIER",
        defense_name_col="TEAM_ABBREVIATION",
        categories=(
            {"id": "hits", "label": "Hits", "threshold": 1.0},
            {"id": "total_bases", "label": "Total bases", "threshold": 1.5},
            {"id": "home_runs", "label": "Home runs", "threshold": 0.5},
            {"id": "rbi", "label": "RBI", "threshold": 0.8},
        ),
        cache_path=_REPO / "Sports/MLB/mlb_stats_cache.csv",
        top3_path=_REPO / "Sports/MLB/data/mlb_hitter_top3_vs_defense.csv",
        opp_metric_label="Opp pitching rank",
        elite_rank_cut=8,
    ),
    "soccer": SportMatchupConfig(
        sport="soccer",
        display_name="Soccer",
        defense_path=_REPO / "Sports/Soccer/cache/soccer_defense_summary.csv",
        defense_team_col="pp_name",
        defense_rank_col="OVERALL_DEF_RANK",
        defense_tier_col="DEF_TIER",
        defense_name_col="team_name",
        categories=(
            {"id": "goals", "label": "Goals", "threshold": 0.4},
            {"id": "assists", "label": "Assists", "threshold": 0.3},
            {"id": "shots", "label": "Shots", "threshold": 2.0},
        ),
        opp_metric_label="Opp goals conceded rank",
        elite_rank_cut=5,
    ),
    "cbb": SportMatchupConfig(
        sport="cbb",
        display_name="CBB",
        defense_path=_REPO / "Sports/CBB/data/reference/cbb_def_rankings.csv",
        defense_team_col="sr_name",
        defense_rank_col="overall_rank",
        defense_tier_col="",
        defense_name_col="sr_name",
        categories=_basketball_categories(),
        cache_path=_REPO / "Sports/CBB/data/cache/cbb_boxscore_cache.csv",
        min_mpg=15.0,
    ),
    "cfb": SportMatchupConfig(
        sport="cfb",
        display_name="CFB",
        defense_path=_REPO / "Sports/CFB/data/reference/cfb_team_unit_rankings.csv",
        defense_team_col="team_abbr",
        defense_rank_col="overall_def_rank_nat",
        defense_tier_col="",
        defense_name_col="team",
        categories=(
            {"id": "pass_yds", "label": "Pass yards", "threshold": 200.0},
            {"id": "rush_yds", "label": "Rush yards", "threshold": 80.0},
            {"id": "rec_yds", "label": "Receiving yards", "threshold": 60.0},
        ),
        cache_path=_REPO / "Sports/CFB/data/cache/cfb_boxscore_cache.csv",
        opp_metric_label="Opp def rank (nat)",
        enabled=True,
    ),
    "nfl": SportMatchupConfig(
        sport="nfl",
        display_name="NFL",
        defense_path=_REPO / "Sports/NFL/data/defense_rankings.csv",
        defense_team_col="team",
        defense_rank_col="pass_def_rank",
        defense_tier_col="",
        defense_name_col="team_abbr",
        categories=(
            {"id": "pass_yds", "label": "Pass yards", "threshold": 200.0},
            {"id": "rush_yds", "label": "Rush yards", "threshold": 60.0},
            {"id": "rec_yds", "label": "Receiving yards", "threshold": 50.0},
        ),
        opp_metric_label="Opp pass def rank",
        enabled=True,
    ),
    "tennis": SportMatchupConfig(
        sport="tennis",
        display_name="Tennis",
        defense_path=_REPO / "Sports/Tennis/cache/tennis_rankings.json",
        defense_team_col="player_key",
        defense_rank_col="rank",
        defense_tier_col="",
        defense_name_col="player",
        categories=(
            {"id": "match_total_games", "label": "Total games", "threshold": 22.0},
            {"id": "games_won", "label": "Games won", "threshold": 12.0},
            {"id": "aces", "label": "Aces", "threshold": 5.0},
            {"id": "double_faults", "label": "Double faults", "threshold": 3.0},
            {"id": "break_points_won", "label": "Break points won", "threshold": 4.0},
        ),
        matchup_mode="player",
        opp_metric_label="Opponent ATP rank",
        elite_rank_cut=25,
        enabled=True,
    ),
}

ENABLED_SPORTS: tuple[str, ...] = tuple(k for k, v in SPORT_CONFIGS.items() if v.enabled and v.categories)
