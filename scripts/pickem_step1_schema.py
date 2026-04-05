"""Shared PrizePicks-style step1 CSV columns for alternate books (Underdog, DraftKings, etc.)."""

from __future__ import annotations

# Matches NBA step1_fetch_prizepicks_api.py column order + provider tags.
STEP1_PICKEM_COLUMNS = [
    "projection_id",
    "pp_projection_id",
    "player_id",
    "pp_game_id",
    "start_time",
    "player",
    "pos",
    "team",
    "opp_team",
    "prop_type",
    "line",
    "pick_type",
    "pp_home_team",
    "pp_away_team",
    "image_url",
]

# Extra columns appended after the PP-shaped block (step2+ should still work).
PROVIDER_EXTRA_COLUMNS = [
    "source_book",
    "ud_sport_id",
    "ud_line_id",
    "ud_stat_key",
]

OUTPUT_COLUMNS = STEP1_PICKEM_COLUMNS + PROVIDER_EXTRA_COLUMNS
