#requires -Version 7.2
<#
.SYNOPSIS
  Re-run NBA steps 1–8 for a slate date so Tier labels match current step7_rank_props.py,
  snapshot step8 under outputs\<date>\, then re-grade that slate.

.DESCRIPTION
  Tiers (A–D) are assigned in NBA\scripts\step7_rank_props.py from rank_score thresholds.
  Graded HTML reads Tier from the slate workbook passed to slate_grader — usually
  outputs\<date>\step8_nba_direction_clean_<date>.xlsx or the latest NBA step8.

  Use this after changing tier logic or to refresh a historical day without manually
  copying files.

.EXAMPLE
  pwsh -File scripts\re_tier_nba_slate.ps1 -Date 2026-04-30

.EXAMPLE
  pwsh -File scripts\re_tier_nba_slate.ps1 -Date 2026-04-30 -SkipFetch

.EXAMPLE
  # Replay tiers without PrizePicks (use a saved step1 CSV from that slate day):
  pwsh -File scripts\re_tier_nba_slate.ps1 -Date 2026-04-30 -Step1Archive "H:\backups\step1_2026-04-30.csv"
#>
param(
    [string]$Date = "",
    [switch]$SkipFetch,
    [string]$Step1Archive = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$Pipeline = Join-Path $Root "run_pipeline.ps1"
$Grader = Join-Path $Root "scripts\run_grader.ps1"

if (-not $Date) {
    $Date = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
}

if ($Date -notmatch "^\d{4}-\d{2}-\d{2}$") {
    Write-Error "Invalid -Date (use YYYY-MM-DD)"
}

if (-not (Test-Path $Pipeline)) {
    Write-Error "Missing run_pipeline.ps1 at repo root: $Pipeline"
}
if (-not (Test-Path $Grader)) {
    Write-Error "Missing scripts\run_grader.ps1"
}

Write-Host ""
Write-Host "=== Re-tier NBA slate: $Date ===" -ForegroundColor Cyan
Write-Host ""

$Step1Out = Join-Path $Root "NBA\data\outputs\step1_pp_props_today.csv"
if ($Step1Archive) {
    if (-not (Test-Path -LiteralPath $Step1Archive)) {
        Write-Error "Step1Archive not found: $Step1Archive"
    }
    $step1Dir = Split-Path $Step1Out -Parent
    if (-not (Test-Path $step1Dir)) {
        New-Item -ItemType Directory -Force -Path $step1Dir | Out-Null
    }
    Copy-Item -LiteralPath $Step1Archive -Destination $Step1Out -Force
    Write-Host "[INFO] Seeded step1 from -Step1Archive (using -SkipFetch for pipeline)." -ForegroundColor Yellow
    $SkipFetch = $true
}

$argsPipe = @("-NoProfile", "-File", $Pipeline, "-Date", $Date, "-NBAOnly")
if ($SkipFetch) {
    $argsPipe += "-SkipFetch"
    Write-Host "[INFO] -SkipFetch: using $Step1Out" -ForegroundColor Yellow
}

& pwsh @argsPipe
# run_pipeline.ps1 often ends with plain `exit` (code 0) even when the NBA block printed FAILED — verify outputs.
$runFlag = Join-Path $Root "NBA\RUN_COMPLETE.flag"
$step7   = Join-Path $Root "NBA\data\outputs\step7_ranked_props.xlsx"
if (-not (Test-Path -LiteralPath $runFlag) -or -not (Test-Path -LiteralPath $step7)) {
    Write-Error @"
NBA pipeline did not complete successfully (missing RUN_COMPLETE.flag and/or step7_ranked_props.xlsx).
PrizePicks step1 for old dates may return 0 rows — use a saved step1 CSV:
  -Step1Archive path\to\step1_$Date.csv
then this script will seed NBA\data\outputs\step1_pp_props_today.csv and run with -SkipFetch.
"@
}

$OutDir = Join-Path $Root "outputs\$Date"
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

function Copy-FirstStep8 {
    param(
        [string[]]$Candidates,
        [string]$Destination
    )
    foreach ($c in $Candidates) {
        if (Test-Path -LiteralPath $c) {
            Copy-Item -LiteralPath $c -Destination $Destination -Force
            return $c
        }
    }
    return $null
}

$nbaMain = Copy-FirstStep8 @(
    (Join-Path $Root "NBA\data\outputs\step8_all_direction_clean.xlsx"),
    (Join-Path $Root "NBA\step8_all_direction_clean.xlsx")
) (Join-Path $OutDir "step8_nba_direction_clean_$Date.xlsx")

if (-not $nbaMain) {
    Write-Error "NBA step8 not found after pipeline (expected step8_all_direction_clean.xlsx)."
}
Write-Host "[OK] Dated NBA slate -> outputs\$Date\step8_nba_direction_clean_$Date.xlsx (from $(Split-Path $nbaMain -Leaf))" -ForegroundColor Green

$h1 = Copy-FirstStep8 @(
    (Join-Path $Root "NBA\step8_nba1h_direction_clean.xlsx"),
    (Join-Path $Root "NBA\data\outputs\step8_nba1h_direction_clean.xlsx")
) (Join-Path $OutDir "step8_nba1h_direction_clean_$Date.xlsx")
if ($h1) {
    Write-Host "[OK] NBA1H snapshot -> step8_nba1h_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}

$q1 = Copy-FirstStep8 @(
    (Join-Path $Root "NBA\step8_nba1q_direction_clean.xlsx"),
    (Join-Path $Root "NBA\data\outputs\step8_nba1q_direction_clean.xlsx")
) (Join-Path $OutDir "step8_nba1q_direction_clean_$Date.xlsx")
if ($q1) {
    Write-Host "[OK] NBA1Q snapshot -> step8_nba1q_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== Re-running grader for $Date ===" -ForegroundColor Cyan
& pwsh -NoProfile -File $Grader -Date $Date
if ($LASTEXITCODE -ne 0) {
    Write-Error "run_grader.ps1 failed (exit $LASTEXITCODE)"
}

Write-Host ""
Write-Host "Done. slate_eval_$Date.html and graded_props_$Date.json updated under ui_runner\templates (if grader completed)." -ForegroundColor Green
