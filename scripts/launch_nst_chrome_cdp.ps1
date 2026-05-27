#requires -Version 5.1
<#
.SYNOPSIS
  Launch Google Chrome with remote debugging for NST (Cloudflare bypass).

.DESCRIPTION
  Opens Chrome on port 9223 with the PropOracle NST profile (~/.nst_browser_profile).
  Log in at naturalstattrick.com, complete Cloudflare challenge once, then run:
    py Sports/NHL/scripts/refresh_nst_cache.py --cdp http://127.0.0.1:9223

.EXAMPLE
  pwsh -NoProfile -File scripts\launch_nst_chrome_cdp.ps1
#>
param(
    [int]$Port = 9223,
    [string]$UserDataDir = ""
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
    $profile = Join-Path $env:USERPROFILE ".nst_browser_profile"
}

$cdpUrl = "http://127.0.0.1:$Port"
$args = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--no-default-browser-check",
    "https://www.naturalstattrick.com/"
)

Write-Host "[NST Chrome] Launching debug Chrome on port $Port" -ForegroundColor Cyan
Write-Host "  Profile: $profile" -ForegroundColor DarkGray
Write-Host "  Log in at naturalstattrick.com, complete Cloudflare challenge once, then run refresh_nst_cache.py --cdp" -ForegroundColor DarkGray
Write-Host "  CDP URL: $cdpUrl" -ForegroundColor DarkGray
Write-Host ""

Start-Process -FilePath $chrome -ArgumentList $args

Start-Sleep -Seconds 2
try {
    $ver = Invoke-RestMethod -Uri "$cdpUrl/json/version" -TimeoutSec 8
    Write-Host "[NST Chrome] CDP ready: $($ver.Browser)" -ForegroundColor Green
} catch {
    Write-Host "[NST Chrome] Started Chrome; CDP not responding yet — wait a few seconds." -ForegroundColor Yellow
}

