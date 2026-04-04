# NHL folder layout (current — do not bulk-move without updating run_pipeline.ps1)
#
#   NHL\outputs\     step1–step8 pipeline CSV/XLSX + nhl_best_tickets.xlsx
#   NHL\cache\       nhl_id_cache.csv, nhl_gamelog_cache.json, nhl_defense_summary.csv, nhl_stats_cache.csv
#   NHL\scripts\     step*.py, bust_gamelog_cache.py, nhl_defense_report.py
#   NHL\docs\        architecture notes (e.g. NHL_Pipeline_Architecture_v1.docx)
#   NHL\scripts\_archive\  deprecated one-offs
#
# Historical: this script used to move loose root files into the above; that layout is now enforced in-repo.

Write-Host "NHL layout is already standardized (outputs\, cache\, scripts\). No action." -ForegroundColor Green
