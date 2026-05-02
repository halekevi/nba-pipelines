#requires -Version 7.2
<#
.SYNOPSIS
  Re-tier NBA slates (no PrizePicks fetch) for yesterday and today by default.

.DESCRIPTION
  Calls scripts\re_tier_nba_no_fetch.ps1 once per date (tier bands + step8 + optional grader).
  Each date only updates rows on ALL whose start_time matches that calendar day (--only-date).

.PARAMETER Dates
  Explicit list YYYY-MM-DD. Default: yesterday + today (local clock).

.PARAMETER SkipGrader
  Passed through — skip run_grader.ps1 for every date.

.EXAMPLE
  pwsh -File scripts\re_tier_nba_slates.ps1

.EXAMPLE
  pwsh -File scripts\re_tier_nba_slates.ps1 -Dates 2026-04-30,2026-05-01
#>
param(
    [string[]]$Dates = @(),
    [switch]$SkipGrader,
    [switch]$TierAllRowsInWorkbook
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$One = Join-Path $PSScriptRoot "re_tier_nba_no_fetch.ps1"

if (-not (Test-Path $One)) {
    Write-Error "Missing re_tier_nba_no_fetch.ps1"
}

if ($Dates.Count -eq 0) {
    $today = Get-Date
    $Dates = @(
        $today.AddDays(-1).ToString("yyyy-MM-dd"),
        $today.ToString("yyyy-MM-dd")
    )
}

Write-Host ""
Write-Host "=== Re-tier NBA slates (no fetch): $($Dates -join ', ') ===" -ForegroundColor Cyan
Write-Host ""

foreach ($d in $Dates) {
    if ($d -notmatch "^\d{4}-\d{2}-\d{2}$") {
        Write-Warning "Skip invalid date: $d"
        continue
    }
    Write-Host ""
    Write-Host ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>" -ForegroundColor Magenta
    Write-Host "  Slate date: $d" -ForegroundColor Magenta
    Write-Host ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>" -ForegroundColor Magenta

    $childArgs = @("-NoProfile", "-File", $One, "-Date", $d)
    if ($SkipGrader) { $childArgs += "-SkipGrader" }
    if ($TierAllRowsInWorkbook) { $childArgs += "-TierAllRowsInWorkbook" }

    & pwsh @childArgs
    # re_tier_nba_no_fetch exits 0 even when step7 has no rows for date (warning only)
}

Write-Host ""
Write-Host "=== All slate passes finished ===" -ForegroundColor Green
