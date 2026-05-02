# PropOracle NBA Pipeline - Organized Structure

## 📁 Folder Layout

```
NBA/
├── scripts/              # All Python pipeline scripts
│   ├── step1_fetch_prizepicks_api.py
│   ├── step2_attach_picktypes.py
│   ├── step3_attach_defense.py
│   ├── step4_attach_player_stats_espn_cache.py
│   ├── step5_add_line_hit_rates.py
│   ├── step6_team_role_context.py
│   ├── step6a_attach_opponent_stats_NBA.py
│   ├── step6b_attach_game_context.py
│   ├── step6c_schedule_flags.py
│   ├── step6d_attach_h2h_matchups.py  ← NEW: Head-to-head last game stats
│   ├── step7_rank_props.py
│   ├── step8_add_direction_context.py
│   ├── step9_build_tickets.py
│   ├── nba_grader.py
│   ├── defense_report.py
│   └── fix_step4_stats.py
│
├── data/
│   ├── cache/            # ESPN cache & reference data
│   │   ├── nba_espn_boxscore_cache.csv (CRITICAL)
│   │   ├── nba_to_espn_id_map.csv      (CRITICAL)
│   │   └── defense_team_summary.csv
│   │
│   ├── inputs/           # Source data
│   │   ├── actuals_nba_*.csv
│   │   ├── step1_pp_props_today.csv
│   │   └── *debug*.csv
│   │
│   └── outputs/          # Pipeline outputs
│       ├── step2_with_picktypes.csv
│       ├── step3_with_defense.csv
│       ├── ...
│       ├── step8_all_direction.csv ← FINAL SLATE
│       ├── step8_all_direction_clean.xlsx
│       └── best_tickets.xlsx
│
├── docs/                 # Documentation
│   ├── README.md
│   ├── SOLUTION_SUMMARY.md
│   └── *.md
│
└── archive/              # Old runs, backups
    ├── old_runs/
    ├── old_csv/
    └── ...
```

## 🚀 Quick Start

From PropOracle root:

`powershell
cd NBA

# Full pipeline
..\run_pipeline.ps1 -Date 2026-03-09

# Or run individual steps
py -3.14 scripts\step2_attach_picktypes.py --input data\inputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv
`

## 📊 Pipeline Steps

| Step | Script | Purpose |
|------|--------|---------|
| 1 | fetch_prizepicks_api.py | Fetch daily props |
| 2 | attach_picktypes.py | Add pick types |
| 3 | attach_defense.py | Add opponent defense |
| 4 | attach_player_stats_espn_cache.py | Add player stats |
| 5 | add_line_hit_rates.py | Add hit rates |
| 6 | team_role_context.py | Add player roles |
| 6a | attach_opponent_stats_NBA.py | Add opponent context |
| 6b | attach_game_context.py | Add Vegas lines |
| 6c | schedule_flags.py | Add B2B flags |
| **6d** | **attach_h2h_matchups.py** | **NEW: Last game vs opponent** |
| 7 | rank_props.py | Rank & tier |
| 8 | add_direction_context.py | Final direction |
| 9 | build_tickets.py | Generate tickets |

## 🔑 Critical Files (DO NOT DELETE)

`
data/cache/nba_espn_boxscore_cache.csv     ← ESPN player stats
data/cache/nba_to_espn_id_map.csv          ← Player name mapping
`

## ✨ New: H2H Matchups (Step 6d)

Finds each player's **last actual game vs the opponent team** and pulls that stat value.

- Fill rate: ~10% (only players with prior history)
- Columns: h2h_last_stat, h2h_last_date, h2h_games_vs_opp

**Last Updated:** 2026-03-08
