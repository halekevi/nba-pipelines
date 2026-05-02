#requires -Version 7.2
<#
.SYNOPSIS
  Refresh all sport defense summary CSVs (quintile DEF_TIER labels) before a pipeline run.

.DESCRIPTION
  Run from repo root after changing utils/defense_tiers.py or defense_report logic.
  Then run:  .\run_pipeline.ps1  (or  .\run_pipeline.ps1 -SkipFetch  to reuse step1 fetches)

  Refreshes:
    - NBA   -> NBA/data/cache/defense_team_summary.csv
    - WNBA  -> WNBA/wnba_defense_summary.csv
    - MLB   -> MLB/mlb_defense_summary.csv   (statsapi team pitching + DEF_TIER)
    - NHL   -> NHL/cache/nhl_defense_summary.csv
    - Soccer-> Soccer/cache/soccer_defense_summary.csv

  NFL defense rankings are produced by NFL step4 (run via full NFL or parallel pipeline).
  CBB uses CBB reference CSVs (separate from this script).
#>
param(
    [switch]$SkipWNBA
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch { }

if (Test-Path (Join-Path $Root ".venv\Scripts\Activate.ps1")) {
    & (Join-Path $Root ".venv\Scripts\Activate.ps1")
}

function Invoke-DefenseStep {
    param(
        [string]$Label,
        [string]$WorkDir,
        [string]$ScriptRel,
        [string]$Arguments = ""
    )
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $WorkDir
    try {
        $cmd = if ($Arguments) { "py -3.14 `"$ScriptRel`" $Arguments" } else { "py -3.14 `"$ScriptRel`"" }
        Write-Host "      $cmd" -ForegroundColor DarkGray
        $output = Invoke-Expression $cmd 2>&1
        foreach ($line in $output) { Write-Host "      $line" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed (exit $LASTEXITCODE)"
        }
        Write-Host "      OK" -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "[ REGENERATE DEFENSE CACHES ]  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host ""

$ok = $true
try {
    Invoke-DefenseStep "NBA defense_team_summary (nba_api)" `
        (Join-Path $Root "NBA") `
        ".\scripts\defense_report.py" `
        "--season 2025-26 --out data\cache\defense_team_summary.csv"

    if (-not $SkipWNBA) {
        Invoke-DefenseStep "WNBA wnba_defense_summary (ESPN)" `
            (Join-Path $Root "WNBA") `
            ".\defense_report.py" `
            "--season 2026 --out wnba_defense_summary.csv"
    } else {
        Write-Host "  [skip] WNBA (-SkipWNBA)" -ForegroundColor DarkGray
    }

    Invoke-DefenseStep "MLB mlb_defense_summary (MLB Stats API)" `
        (Join-Path $Root "MLB") `
        ".\scripts\mlb_defense_report.py" `
        "--out mlb_defense_summary.csv"

    Invoke-DefenseStep "NHL nhl_defense_summary (NHL API)" `
        (Join-Path $Root "NHL") `
        ".\scripts\nhl_defense_report.py" `
        "--out cache\nhl_defense_summary.csv"

    Invoke-DefenseStep "Soccer soccer_defense_summary (ESPN)" `
        (Join-Path $Root "Soccer") `
        ".\scripts\soccer_defense_report.py" `
        "--out cache\soccer_defense_summary.csv"
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    $ok = $false
}

Write-Host ""
if ($ok) {
    Write-Host "  Defense caches refreshed. Next: .\run_pipeline.ps1  or  .\run_pipeline.ps1 -SkipFetch" -ForegroundColor Green
} else {
    Write-Host "  Finished with errors." -ForegroundColor Red
    exit 1
}
