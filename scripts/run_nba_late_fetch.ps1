#requires -Version 5.1
<#
.SYNOPSIS
  Re-fetch NBA PrizePicks props and run NBA pipeline + combined tickets (mid-morning board).
.NOTES
  Safe to run manually anytime. Intended for Task Scheduler ~11:00 ET after PrizePicks posts NBA props.
  Invokes repo-root run_pipeline.ps1 with -NBAOnly -SkipFetch (step1 done here with --replace).
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Host "[NBA_LATE_FETCH] Starting NBA re-fetch $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

$NBADir = Join-Path $Root "NBA"
$step1Args = @(
    "--league_id", "7",
    "--game_mode", "pickem",
    "--per_page", "250",
    "--max_pages", "5",
    "--sleep", "2.0",
    "--cooldown_seconds", "90",
    "--max_cooldowns", "3",
    "--jitter_seconds", "10.0",
    "--replace",
    "--output", "data\outputs\step1_pp_props_today.csv"
)

Push-Location $NBADir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_api.py" @step1Args
}
finally {
    Pop-Location
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "[NBA_LATE_FETCH] step1 failed, aborting (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

$pipeScript = Join-Path $Root "run_pipeline.ps1"
if (-not (Test-Path $pipeScript)) {
    Write-Host "[NBA_LATE_FETCH] Missing run_pipeline.ps1 at $pipeScript" -ForegroundColor Red
    exit 1
}

& pwsh -NoProfile -File $pipeScript -NBAOnly -SkipFetch
if ($LASTEXITCODE -ne 0) {
    Write-Host "[NBA_LATE_FETCH] pipeline failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

Write-Host "[NBA_LATE_FETCH] Done $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
exit 0
