#requires -Version 5.1
<#
.SYNOPSIS
  Launch Google Chrome with remote debugging for PrizePicks (DataDome bypass).

.DESCRIPTION
  Opens Chrome on port 9222 with the PropOracle PrizePicks profile (~/.pp_browser_profile).
  Log in at app.prizepicks.com, complete any "Press & Hold" challenge, then run pipelines with:
    -Cdp http://127.0.0.1:9222
  or set PROPORACLE_PP_CDP / PRIZEPICKS_CDP to that URL.

.EXAMPLE
  pwsh -NoProfile -File scripts\launch_prizepicks_chrome_cdp.ps1
  pwsh -NoProfile -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3
#>
param(
    [int]$Port = 9222,
    [string]$UserDataDir = "",
    [switch]$OpenBoard,
    [string]$LeagueId = "3"
)

$ErrorActionPreference = "Stop"

$chromeCandidates = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Host "Chrome not found. Install Chrome or pass a full path via -ChromeExe (not implemented)." -ForegroundColor Red
    exit 1
}

$profile = $UserDataDir.Trim()
if (-not $profile) {
    $profile = Join-Path $env:USERPROFILE ".pp_browser_profile"
}

$cdpUrl = "http://127.0.0.1:$Port"
$args = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--no-default-browser-check"
)
if ($OpenBoard) {
    $args += "https://app.prizepicks.com/board?league_id=$LeagueId"
} else {
    $args += "https://app.prizepicks.com/"
}

Write-Host "[PP Chrome] Launching debug Chrome on port $Port" -ForegroundColor Cyan
Write-Host "  Profile: $profile" -ForegroundColor DarkGray
Write-Host "  After login / human check, set: `$env:PROPORACLE_PP_CDP = '$cdpUrl'" -ForegroundColor DarkGray
Write-Host ""

Start-Process -FilePath $chrome -ArgumentList $args

Start-Sleep -Seconds 2
try {
    $ver = Invoke-RestMethod -Uri "$cdpUrl/json/version" -TimeoutSec 8
    Write-Host "[PP Chrome] CDP ready: $($ver.Browser)" -ForegroundColor Green
} catch {
    Write-Host "[PP Chrome] Started Chrome; CDP not responding yet — wait a few seconds." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "WNBA board: league_id=3  |  NBA board: league_id=7" -ForegroundColor DarkGray
Write-Host "Run pipeline: pwsh -File scripts\run_wnba_pipeline.ps1 -Cdp $cdpUrl -Date YYYY-MM-DD" -ForegroundColor DarkGray
