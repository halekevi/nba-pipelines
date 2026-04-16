#requires -Version 5.1
param()

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$Daily = Join-Path $Root "scripts\run_daily.ps1"
$Snapshot = Join-Path $Root "scripts\log_prop_snapshot.ps1"

if (-not (Test-Path $Daily)) {
    Write-Error "Missing daily script: $Daily"
    exit 1
}
if (-not (Test-Path $Snapshot)) {
    Write-Error "Missing prop snapshot script: $Snapshot"
    exit 1
}

Set-Location $Root
Write-Host "[7AM DAILY] Pulling latest repository..." -ForegroundColor Cyan
git pull --ff-only 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[7AM DAILY] git pull failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[7AM DAILY] Running run_daily.ps1 (-SkipGrader)..." -ForegroundColor Cyan
& pwsh -NoProfile -File $Daily -SkipGrader
$dailyExit = $LASTEXITCODE

Write-Host "[7AM DAILY] Logging fetched prop snapshot..." -ForegroundColor Cyan
& pwsh -NoProfile -File $Snapshot -Label "7AM DAILY POST" -CompareToState -WriteState
if ($LASTEXITCODE -ne 0) {
    Write-Host "[7AM DAILY] Snapshot logging failed" -ForegroundColor Yellow
}

if ($dailyExit -ne 0) {
    Write-Host "[7AM DAILY] run_daily failed (exit $dailyExit)" -ForegroundColor Red
    exit $dailyExit
}

Write-Host "[7AM DAILY] Complete" -ForegroundColor Green
exit 0
