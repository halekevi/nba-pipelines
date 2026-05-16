#requires -Version 5.1
<#
.SYNOPSIS
  WNBA step1 via Chrome CDP — bypasses PrizePicks DataDome when direct API returns 403.

.NOTES
  1) pwsh -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3
  2) Complete login + "Press & Hold" in that Chrome window (board must load).
  3) pwsh -File scripts\run_wnba_step1_chrome_debug.ps1 -Date 2026-05-16

  See docs\chrome_debug_setup.md and docs\guides\BROWSER_FETCH_SETUP.md
#>
param(
    [string]$CdpUrl = "http://127.0.0.1:9222",
    [string]$Date = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$WNBADir = Join-Path $Root "Sports\WNBA"
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Test-CdpEndpoint {
    param([string]$BaseUrl)
    try {
        $u = ($BaseUrl.TrimEnd("/")) + "/json/version"
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (-not (Test-CdpEndpoint -BaseUrl $CdpUrl)) {
    Write-Host ""
    Write-Host "[WNBA CDP] No Chrome debugger at $CdpUrl" -ForegroundColor Yellow
    Write-Host "  pwsh -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3" -ForegroundColor Cyan
    Write-Host "  Then solve Press & Hold on the board and re-run this script." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$targetDate = $Date.Trim()
if (-not $targetDate) {
    try {
        $tzEt = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
        $targetDate = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tzEt).ToString("yyyy-MM-dd")
    } catch {
        $targetDate = (Get-Date).ToString("yyyy-MM-dd")
    }
}

$outPath = $Output.Trim()
if (-not $outPath) {
    $outPath = Join-Path $Root "outputs\$targetDate\wnba\step1_wnba_props.csv"
} elseif (-not [System.IO.Path]::IsPathRooted($outPath)) {
    $outPath = Join-Path $Root $outPath
}
$outDir = Split-Path -Parent $outPath
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

Write-Host "[WNBA CDP] $CdpUrl | date=$targetDate | output=$outPath" -ForegroundColor Cyan

Push-Location $WNBADir
try {
    & py -3.14 -u ".\step1_fetch_prizepicks.py" `
        --cdp $CdpUrl `
        --league_id 3 `
        --timeout 120 `
        --game_mode pickem `
        --per_page 250 `
        --max_pages 10 `
        --output $outPath `
        --date $targetDate
} finally {
    Pop-Location
}

exit $LASTEXITCODE
