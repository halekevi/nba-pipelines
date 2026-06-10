"""Columns for 70% stack / opponent context — persisted through step8 and graded exports."""

from __future__ import annotations

# Internal (snake_case) names used in step7/step8 pipelines.
STACK_CONTEXT_COLS: tuple[str, ...] = (
    "consistency_grade",
    "team_top3_rank",
    "team_bottom3_rank",
    "def_boost_hist",
    "top3_weak_overperformer",
    "top3_elite_fader",
    "top3_def_context",
    "top3_under_context",
)

# Excel / Full Slate display headers (NBA-style step8 rename).
# Persisted on graded JSON / Box Raw for stack backtests and tickets.
GRADED_SIGNAL_COLS: tuple[str, ...] = (
    "def_tier",
    "l5_over",
    "l5_under",
    "l10_over",
    "l10_under",
    "l10_games_played",
    "l10_streak",
    "hit_rate",
    "strat_hit_rate",
    "strat_n",
    "hit_rate_l5",
    "hit_rate_l10",
    "player_hr_historical",
    "opp_hr_historical",
    *STACK_CONTEXT_COLS,
)

STACK_CONTEXT_RENAME: dict[str, str] = {
    "consistency_grade": "Consistency Grade",
    "team_top3_rank": "Top3 Rank",
    "team_bottom3_rank": "Bottom3 Rank",
    "def_boost_hist": "Def Boost Hist",
    "top3_weak_overperformer": "Top3 Weak Over",
    "top3_elite_fader": "Top3 Elite Fade",
    "top3_def_context": "Top3 Def Context",
    "top3_under_context": "Top3 Under Context",
}
