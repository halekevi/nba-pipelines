#requires -Version 7.2
<#
.SYNOPSIS
  Re-apply NBA tier bands from existing step7_ranked_props.xlsx (no PrizePicks fetch),
  rebuild step8 clean slate, copy to outputs\<date>\, optionally run grader.

.PARAMETER Date
  Slate date YYYY-MM-DD (passed to step8 date filter + dated step8 copy).

.PARAMETER SkipGrader
  Only rebuild step7 tiers + step8; do not run run_grader.ps1.

.PARAMETER TierAllRowsInWorkbook
  Recompute tier for every row on ALL (omit --only-date). Default is --only-date matching -Date.

.EXAMPLE
  pwsh -File scripts\re_tier_nba_no_fetch.ps1 -Date 2026-04-30
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Date,
    [switch]$SkipGrader,
    [switch]$TierAllRowsInWorkbook
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$SportsRoot = Join-Path $Root "Sports"

if ($Date -notmatch "^\d{4}-\d{2}-\d{2}$") {
    Write-Error "Use -Date YYYY-MM-DD"
}

$PyRetier = Join-Path $Root "scripts\re_apply_nba_tiers_from_step7.py"
$Step8 = Join-Path $Root "Sports\NBA\scripts\step8_add_direction_context.py"
$Grader = Join-Path $Root "scripts\run_grader.ps1"

foreach ($p in @($PyRetier, $Step8, $Grader)) {
    if (-not (Test-Path $p)) {
        Write-Error "Missing: $p"
    }
}

Write-Host "=== Re-tier NBA (no fetch) — slate $Date ===" -ForegroundColor Cyan

Set-Location (Join-Path $SportsRoot "NBA")
try {
    if (-not $TierAllRowsInWorkbook) {
        & py -3.14 -X utf8 $PyRetier "--only-date" $Date
    }
    else {
        & py -3.14 -X utf8 $PyRetier
    }
    if ($LASTEXITCODE -eq 5) {
        Write-Warning "step7 ALL has no rows with start_time on $Date — skipping step8 + grader for this slate."
        exit 0
    }
    if ($LASTEXITCODE -ne 0) { throw "re_apply_nba_tiers_from_step7.py exit $LASTEXITCODE" }

    & py -3.14 -X utf8 $Step8 `
        --input "data\outputs\step7_ranked_props.xlsx" `
        --sheet ALL `
        --output "data\outputs\step8_all_direction.csv" `
        --date $Date
    if ($LASTEXITCODE -ne 0) { throw "step8_add_direction_context.py exit $LASTEXITCODE" }
}
finally {
    Set-Location $Root
}

$srcClean = Join-Path $Root "Sports\NBA\data\outputs\step8_all_direction_clean.xlsx"
foreach ($dst in @(
        (Join-Path $Root "Sports\NBA\step8_all_direction_clean.xlsx"),
        (Join-Path $Root "outputs\$Date\step8_nba_direction_clean_$Date.xlsx")
    )) {
    if (-not (Test-Path $srcClean)) {
        Write-Warning "Expected step8 clean xlsx missing: $srcClean"
        break
    }
    $dir = Split-Path $dst -Parent
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Copy-Item -LiteralPath $srcClean -Destination $dst -Force
    Write-Host "[OK] $dst" -ForegroundColor Green
}

if (-not $SkipGrader) {
    Write-Host "=== Grader — $Date ===" -ForegroundColor Cyan
    & pwsh -NoProfile -File $Grader -Date $Date
    if ($LASTEXITCODE -ne 0) {
        Write-Error "run_grader.ps1 exit $LASTEXITCODE"
    }
}

Write-Host "Done." -ForegroundColor Green
