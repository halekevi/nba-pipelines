#requires -Version 5.1
<#
.SYNOPSIS
  MLB step1 via Chrome Remote Debugging (CDP) — uses your real Chrome session so
  PrizePicks sees trusted cookies + TLS instead of the headless direct API (403).

.NOTES
  "Chrome debugger" = --remote-debugging-port (DevTools Protocol).

  1) Close Chrome instances using the same profile.
  2) Start Chrome (example — fix path if Chrome is installed elsewhere):

     & "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" `
         --remote-debugging-port=9222 `
         --user-data-dir="$env:USERPROFILE\.pp_browser_profile"

  3) In that window: https://app.prizepicks.com/ — log in, clear any challenge.
  4) From repo root:

     pwsh -NoProfile -File scripts\run_mlb_step1_chrome_debug.ps1

  Requires: py -3.14, Playwright (+ browsers) for step1_fetch_prizepicks_mlb.py --cdp
#>
param(
    [string]$CdpUrl = "http://127.0.0.1:9222",
    [string]$Date = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$SportsRoot = Join-Path $Root "Sports"
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Test-CdpEndpoint {
    param([string]$BaseUrl)
    try {
        $u = ($BaseUrl.TrimEnd("/")) + "/json/version"
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    }
    catch {
        return $false
    }
}

if (-not (Test-CdpEndpoint -BaseUrl $CdpUrl)) {
    Write-Host ""
    Write-Host "[MLB CDP] No Chrome debugger at $CdpUrl (GET .../json/version failed)." -ForegroundColor Yellow
    Write-Host "Start Chrome with a debug port, then log in on PrizePicks, then re-run this script." -ForegroundColor Yellow
    Write-Host ""
    Write-Host '  & "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" `'
    Write-Host '      --remote-debugging-port=9222 `'
    Write-Host '      --user-data-dir="$env:USERPROFILE\.pp_browser_profile"'
    Write-Host ""
    Write-Host "Full walkthrough: docs\chrome_debug_setup.md (MLB step1 over CDP)" -ForegroundColor DarkGray
    exit 1
}

$targetDate = $Date.Trim()
if (-not $targetDate) {
    try {
        $tzEt = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
        $targetDate = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tzEt).ToString("yyyy-MM-dd")
    }
    catch {
        $targetDate = (Get-Date).ToString("yyyy-MM-dd")
    }
}

$outPath = $Output.Trim()
if (-not $outPath) {
    $outPath = Join-Path $Root "outputs\$targetDate\mlb\step1_mlb_props.csv"
    $outDir = Split-Path $outPath -Parent
    if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
}
elseif (-not [System.IO.Path]::IsPathRooted($outPath)) {
    $outPath = Join-Path $Root $outPath
}

$MLBDir = Join-Path $SportsRoot "MLB"
Write-Host "[MLB CDP] Using $CdpUrl | date=$targetDate | output=$outPath" -ForegroundColor Cyan

Push-Location $MLBDir
try {
    & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
        --cdp $CdpUrl `
        --date $targetDate `
        --output $outPath
}
finally {
    Pop-Location
}

exit $LASTEXITCODE
