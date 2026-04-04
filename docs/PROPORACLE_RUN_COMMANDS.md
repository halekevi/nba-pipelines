# propORacle Run Commands (PowerShell)

This is a single reference for the most common PowerShell run commands in this repo.

## Open PowerShell in Project Root

```powershell
cd "C:\Users\halek\OneDrive\Desktop\PropORACLE"
```

## Main Pipeline (`run_pipeline.ps1` at repo root)

```powershell
# Full parallel run (NBA + CBB + NHL + Soccer + Combined)
.\run_pipeline.ps1

# Specific date
.\run_pipeline.ps1 -Date 2026-03-20

# Sport-only runs
.\run_pipeline.ps1 -NBAOnly
.\run_pipeline.ps1 -CBBOnly
.\run_pipeline.ps1 -NHLOnly
.\run_pipeline.ps1 -MLBOnly
.\run_pipeline.ps1 -SoccerOnly
.\run_pipeline.ps1 -WNBAOnly

# Combined slate only (from existing sport outputs)
.\run_pipeline.ps1 -CombinedOnly

# Skip step1 fetch for whichever sport(s) run
.\run_pipeline.ps1 -SkipFetch
.\run_pipeline.ps1 -NBAOnly -SkipFetch
.\run_pipeline.ps1 -NHLOnly -SkipFetch
.\run_pipeline.ps1 -SoccerOnly -SkipFetch

# Cache controls (NBA ESPN cache)
.\run_pipeline.ps1 -RefreshCache
.\run_pipeline.ps1 -CacheAgeDays 7

# Optional API key override for game context step
.\run_pipeline.ps1 -NBAOnly -OddsApiKey "YOUR_ODDS_API_KEY"
```

## Grader Runner (`scripts/run_grader.ps1`)

```powershell
# Grade yesterday (default)
.\scripts\run_grader.ps1

# Grade specific date
.\scripts\run_grader.ps1 -Date 2026-03-19
```

## WNBA Runner (`scripts/run_wnba_pipeline.ps1`)

```powershell
.\scripts\run_wnba_pipeline.ps1
.\scripts\run_wnba_pipeline.ps1 -Date 2026-07-15
.\scripts\run_wnba_pipeline.ps1 -RefreshCache
.\scripts\run_wnba_pipeline.ps1 -SkipFetch
```

## Soccer Runner (`Soccer/scripts/run_soccer_pipeline.ps1`)

```powershell
.\Soccer\scripts\run_soccer_pipeline.ps1
.\Soccer\scripts\run_soccer_pipeline.ps1 -SkipFetch
.\Soccer\scripts\run_soccer_pipeline.ps1 -LeagueId 1234
.\Soccer\scripts\run_soccer_pipeline.ps1 -NTeams 20
```

## Daily Grades (`scripts/daily_grades.ps1`)

```powershell
.\scripts\daily_grades.ps1
.\scripts\daily_grades.ps1 -Date 2026-03-19
```

## Register Scheduled Task (`scripts/Register_Daily_Task.ps1`)

```powershell
# Run once in elevated PowerShell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
.\scripts\Register_Daily_Task.ps1

# Test scheduled task now
Start-ScheduledTask -TaskName "PropOracle - Master Pipeline Daily"

# Check task status
Get-ScheduledTaskInfo -TaskName "PropOracle - Master Pipeline Daily" | Select LastRunTime, LastTaskResult

# Remove task
Unregister-ScheduledTask -TaskName "PropOracle - Master Pipeline Daily" -Confirm:$false
```

## Optional Python Direct Commands

These are usually called by the PowerShell runners above:

```powershell
# Build grade HTML directly
py -3.14 .\scripts\grading\build_grades_html.py --date 2026-03-19 --out .\ui_runner\templates

# Build combined slate tickets directly
py -3.14 .\scripts\combined_slate_tickets.py --help
```

## Notes

- Folder map and what to edit after moving files: [PROJECT_LAYOUT.md](PROJECT_LAYOUT.md).
- Date format: `yyyy-MM-dd` is safest.
- `-SkipFetch` assumes prior step1 output files already exist.
- Full run auto-combines available sport outputs into final tickets.
