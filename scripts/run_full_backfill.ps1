#requires -Version 7.2
<#
.SYNOPSIS
  One-time full historical backfill: fetch logs, grade legs, synthetic graded, rebuild consistency, analyze.
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent

$CacheDir = Join-Path $Root "data\cache"
if (!(Test-Path $CacheDir)) {
    New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    Write-Host "Created local cache directory: $CacheDir" `
      -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Note: data\cache\ is excluded from OneDrive sync."
Write-Host "All databases will be created locally." `
  -ForegroundColor DarkGray
Write-Host ""

Write-Host "=== Prop Oracle Full Historical Backfill ===" -ForegroundColor Cyan
Write-Host "This will take 20-60 minutes depending on player count."
Write-Host "Do not close this window."
Write-Host ""

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Set-Location $Root

Write-Host "[1/5] Fetching historical game logs..." -ForegroundColor Yellow
& py -3.14 (Join-Path $Root "scripts\fetch_historical_actuals.py")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Fetch had errors — check logs\fetch_errors.log"
}

Write-Host "[2/5] Grading entry legs..." -ForegroundColor Yellow
& py -3.14 (Join-Path $Root "scripts\grade_entry_legs.py")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Entry leg grading had errors"
}

Write-Host "[3/5] Building synthetic graded data..." -ForegroundColor Yellow
& py -3.14 (Join-Path $Root "scripts\build_synthetic_graded.py")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Synthetic build had errors"
}
Write-Host "Synthetic data stored in data\cache\synthetic_graded.db" -ForegroundColor DarkGray

Write-Host "[4/5] Rebuilding player consistency database..." -ForegroundColor Yellow
$consistencyScript = Join-Path $Root "scripts\build_player_consistency.py"
& py -3.14 $consistencyScript --rebuild --sources all
if ($LASTEXITCODE -ne 0) {
    Write-Error "Consistency rebuild failed — investigate before running pipeline"
    exit 1
}

Write-Host "[5/5] Running grader analysis..." -ForegroundColor Yellow
& py -3.14 (Join-Path $Root "scripts\analyze_grader.py")

Write-Host ""
Write-Host "=== Backfill complete ===" -ForegroundColor Green
Write-Host "Player consistency DB: data\cache\player_consistency.db"
Write-Host "Run the pipeline to apply new grades to today's slate."

exit 0
