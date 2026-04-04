# PropOracle - Organized Structure

## 📁 Folder Layout

`
PropOracle/
├── scripts/              # All pipeline & utility scripts
│   ├── run_pipeline.ps1
│   ├── run_grader.ps1
│   ├── run_wnba_pipeline.ps1
│   ├── run_cbb_pipeline.ps1
│   ├── run_mlb_pipeline.ps1
│   ├── combined_slate_tickets.py
│   ├── combined_ticket_grader.py
│   ├── build_ticket_eval_html.py
│   └── ...
│
├── data/
│   ├── cache/            # ESPN, Vegas, player mappings
│   │   ├── *_cache.csv
│   │   ├── *_map.csv
│   │   └── defense_team_summary.csv
│   │
│   ├── inputs/           # Source data (actuals, raw props)
│   │   ├── actuals_*.csv
│   │   └── *_props_today.csv
│   │
│   └── outputs/          # Daily pipeline outputs
│       ├── combined_slate_tickets_2026-03-08.xlsx
│       ├── combined_tickets_graded_2026-03-08.xlsx
│       └── ...
│
├── ui_runner/            # Web UI for slate viewer
│   ├── templates/        # HTML templates
│   └── components/       # JSX/React components
│
├── docs/                 # Documentation
│   ├── README.md
│   ├── GUIDES.md
│   ├── .gitignore
│   └── *.md
│
├── config/               # Configuration files
│   └── settings.json (future)
│
├── NBA/                  # NBA pipeline (organized)
│   ├── scripts/
│   ├── data/cache/
│   ├── data/inputs/
│   ├── data/outputs/
│   └── ...
│
├── CBB/                  # College Basketball pipeline
├── NHL/                  # Hockey pipeline
├── Soccer/               # Soccer pipeline
├── MLB/                  # Baseball pipeline (if available)
├── WNBA/                 # WNBA pipeline (if available)
│
├── grader/               # Grading utility folder
│
├── outputs/              # Consolidated daily outputs (symlink possible)
│
└── archive/              # Old runs, backups
    ├── old_scripts/
    ├── old_outputs/
    └── old_docs/
`

## 🚀 Quick Start

`powershell
cd "C:\Users\halek\OneDrive\Desktop\Vision Board\PropOracle\PropOracle"

# Run full pipeline
.\scripts\run_pipeline.ps1 -Date 2026-03-09

# Run grader
.\scripts\run_grader.ps1 -Date 2026-03-08

# View combined slate
.\scripts\run_pipeline.ps1 -Date 2026-03-09 | Open data\outputs\combined_slate_tickets_2026-03-09.xlsx
`

## 📊 Sports Pipelines

Each sport has its own organized structure:
- **NBA/** - Basketball (primary)
- **CBB/** - College Basketball
- **NHL/** - Hockey
- **Soccer/** - Soccer/Football
- **MLB/** - Baseball (if enabled)
- **WNBA/** - Women's Basketball (if enabled)

Each follows the same pattern:
`
Sport/
├── scripts/         # step1, step2, ... scripts
├── data/cache/      # Sport-specific cache
├── data/inputs/     # Raw props
└── data/outputs/    # Pipeline outputs
`

## 🔑 Critical Files (DO NOT DELETE)

`
data/cache/nba_espn_boxscore_cache.csv
data/cache/nba_to_espn_id_map.csv
data/cache/defense_team_summary.csv
NBA/data/cache/nba_espn_boxscore_cache.csv
`

## ✨ New Features

- **NBA H2H Matchups (Step 6d)** - Shows last game vs opponent stats
- **Multi-sport support** - NBA, CBB, NHL, Soccer, MLB, WNBA
- **Organized by function** - Scripts, data, UI, docs all in their places
- **Archive structure** - Old runs preserved but out of the way

## 📌 Notes

- All intermediate CSV files can be regenerated
- Cache files should be backed up periodically
- Use 
un_pipeline.ps1 -RefreshCache to rebuild ESPN cache
- Daily tasks can auto-run via Register_Daily_Task.ps1

---

**Last Updated:** 2026-03-08
**Version:** 1.0 Organized
