# Run WNBA step1 attached to an authenticated Chrome session
# Usage:
#   .\scripts\run_wnba_step1_chrome_debug.ps1
#   .\scripts\run_wnba_step1_chrome_debug.ps1 -Date 2026-04-30 -Port 9222

param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [int]$Port = 9222
)

$ErrorActionPreference = "Stop"

$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) { $ScriptPath = $PSCommandPath }
$ScriptDir = Split-Path -Parent $ScriptPath
$Root = Split-Path -Parent $ScriptDir
$SportsRoot = Join-Path $Root "Sports"
$WNBADir = Join-Path $SportsRoot "WNBA"

$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$DebugUrl = "http://127.0.0.1:$Port"
$DebugProfile = Join-Path $env:TEMP "chrome_debug_profile"

if (-not (Test-Path $ChromePath)) {
    throw "Chrome not found at $ChromePath"
}

Write-Host "[WNBA] Launching Chrome with remote debug on port $Port..." -ForegroundColor Cyan
Start-Process $ChromePath -ArgumentList `
    "--remote-debugging-port=$Port", `
    "--user-data-dir=$DebugProfile", `
    "https://app.prizepicks.com/"

Write-Host "[WNBA] Chrome launched. Please:" -ForegroundColor Yellow
Write-Host "  1. Log in to PrizePicks if prompted" -ForegroundColor Yellow
Write-Host "  2. Navigate to the WNBA board" -ForegroundColor Yellow
Write-Host "  3. Solve any captcha/challenge if shown" -ForegroundColor Yellow
Write-Host "  4. Press ENTER here when ready..." -ForegroundColor Yellow
Read-Host | Out-Null

Write-Host "[WNBA] Attaching CDP and fetching projections..." -ForegroundColor Cyan
$env:PYTHONPATH = $Root

Push-Location $Root
try {
    & py -3.14 "WNBA/step1_fetch_prizepicks.py" `
        --playwright `
        --cdp $DebugUrl `
        --date $Date `
        --print-leagues
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    Write-Host "[WNBA] Step1 exited with code $exitCode" -ForegroundColor Red
    exit $exitCode
}

Write-Host "[WNBA] Done. Default output is Sports/WNBA/step1_wnba_props.csv (pipeline uses Sports/WNBA/data/outputs/step1_wnba_props.csv)." -ForegroundColor Green
