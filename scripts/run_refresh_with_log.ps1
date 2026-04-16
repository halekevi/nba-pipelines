#requires -Version 5.1
param(
    [string]$RunLabel = "9AM"
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$LateFetch = Join-Path $Root "scripts\run_nba_late_fetch.ps1"
$Snapshot = Join-Path $Root "scripts\log_prop_snapshot.ps1"

if (-not (Test-Path $LateFetch)) {
    Write-Error "Missing late fetch script: $LateFetch"
    exit 1
}
if (-not (Test-Path $Snapshot)) {
    Write-Error "Missing prop snapshot script: $Snapshot"
    exit 1
}

Set-Location $Root
Write-Host "[REFRESH $RunLabel] Starting $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan

& pwsh -NoProfile -File $Snapshot -Label "$RunLabel PRE" -WriteState
if ($LASTEXITCODE -ne 0) {
    Write-Host "[REFRESH $RunLabel] PRE snapshot logging failed (continuing)" -ForegroundColor Yellow
}

& pwsh -NoProfile -File $LateFetch -NoOverwrite
$refreshExit = $LASTEXITCODE

& pwsh -NoProfile -File $Snapshot -Label "$RunLabel POST" -CompareToState -WriteState
if ($LASTEXITCODE -ne 0) {
    Write-Host "[REFRESH $RunLabel] POST snapshot logging failed" -ForegroundColor Yellow
}

if ($refreshExit -ne 0) {
    Write-Host "[REFRESH $RunLabel] Refresh failed (exit $refreshExit)" -ForegroundColor Red
    exit $refreshExit
}

Write-Host "[REFRESH $RunLabel] Complete" -ForegroundColor Green
exit 0
