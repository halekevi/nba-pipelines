#requires -Version 7.2
<#
.SYNOPSIS
  Re-slate NBA, NHL, MLB, and Soccer with refreshed defense tiers, rebuild combined,
  re-grade the same calendar date, and regenerate grades HTML.

.DESCRIPTION
  Intended after utils/defense_tiers.py / defense cache updates. Uses:
    - regenerate_defense_caches.ps1 (once) unless -SkipDefenseCaches
    - run_pipeline.ps1 per sport with -SkipFetch, -SkipDailyGrader, -SkipPush
      (SkipDailyGrader avoids post-pipeline grader using Date-1; we run_grader explicitly.)
    - run_pipeline.ps1 -CombinedOnly to refresh combined from on-disk step8s
    - scripts/run_grader.ps1 -Date <slate date>
    - scripts/grading/build_grades_html.py --date <slate date>
    - copies slate_eval + graded_props into mobile/www

  Run from repo root is NOT required; script cds to repo root automatically.

.PARAMETER StartDate
  Inclusive yyyy-MM-dd (default 2026-04-21).

.PARAMETER EndDate
  Inclusive yyyy-MM-dd (default 2026-05-04).

.PARAMETER SkipDefenseCaches
  Skip regenerate_defense_caches.ps1 if caches were already refreshed.

.PARAMETER DryRun
  Print NBA dated step8 coverage and exit before any pipeline work.

.PARAMETER AllowMissingStep8
  If any date lacks outputs\<date>\step8_nba_direction_clean_<date>.xlsx, warn but continue.
  Without this switch, the script exits with code 2 when any date is missing (unless -DryRun).

.EXAMPLE
  .\scripts\backfill_def_tier_reslate.ps1
.EXAMPLE
  .\scripts\backfill_def_tier_reslate.ps1 -DryRun
.EXAMPLE
  .\scripts\backfill_def_tier_reslate.ps1 -AllowMissingStep8
#>
param(
    [string]$StartDate = "2026-04-21",
    [string]$EndDate = "2026-05-04",
    [switch]$SkipDefenseCaches,
    [switch]$DryRun,
    [switch]$AllowMissingStep8
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Test-NbaDatedStep8([string]$ds) {
    $p = Join-Path $Root "outputs\$ds\step8_nba_direction_clean_$ds.xlsx"
    return (Test-Path -LiteralPath $p)
}

Write-Host "[check] run_pipeline.ps1 supports -SkipPush (param + git skip block)." -ForegroundColor DarkGray

$start = [datetime]::ParseExact($StartDate, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
$end = [datetime]::ParseExact($EndDate, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
if ($start -gt $end) { throw "StartDate $StartDate must be <= EndDate $EndDate" }

$dates = [System.Collections.Generic.List[string]]::new()
for ($d = $start; $d -le $end; $d = $d.AddDays(1)) {
    $dates.Add($d.ToString("yyyy-MM-dd")) | Out-Null
}

Write-Host ""
Write-Host "[check] NBA dated step8: outputs\<date>\step8_nba_direction_clean_<date>.xlsx" -ForegroundColor Cyan
$missing = [System.Collections.Generic.List[string]]::new()
foreach ($ds in $dates) {
    $ok = Test-NbaDatedStep8 $ds
    Write-Host "  $ds  ->  $ok"
    if (-not $ok) { $missing.Add($ds) | Out-Null }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "[WARN] Missing NBA dated step8 for: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "  -SkipFetch still rebuilds from Sports\NBA\data\outputs\step1 when present;" -ForegroundColor Yellow
    Write-Host "  dates without historical step1 may need a fetch (re-run without -SkipFetch for those days)." -ForegroundColor Yellow
    if (-not $AllowMissingStep8 -and -not $DryRun) {
        Write-Host ""
        Write-Host "Aborting. Re-run with -AllowMissingStep8 to proceed anyway." -ForegroundColor Red
        exit 2
    }
}

if ($DryRun) {
    Write-Host ""
    Write-Host "[DryRun] Stopping before regenerate_defense_caches / pipelines." -ForegroundColor Magenta
    exit 0
}

if (-not $SkipDefenseCaches) {
    $cacheScript = Join-Path $Root "regenerate_defense_caches.ps1"
    if (-not (Test-Path -LiteralPath $cacheScript)) {
        throw "Missing $cacheScript"
    }
    Write-Host ""
    Write-Host "[defense] Running regenerate_defense_caches.ps1 ..." -ForegroundColor Magenta
    & $cacheScript
}

$runPipeline = Join-Path $Root "run_pipeline.ps1"
$runGrader = Join-Path $Root "scripts\run_grader.ps1"
$buildHtml = Join-Path $Root "scripts\grading\build_grades_html.py"

foreach ($ds in $dates) {
    Write-Host ""
    Write-Host "==================== $ds ====================" -ForegroundColor Cyan

    & $runPipeline -Date $ds -SkipFetch -SkipDailyGrader -SkipPush -NBAOnly
    if (-not $?) { throw "run_pipeline NBAOnly failed for $ds" }

    & $runPipeline -Date $ds -SkipFetch -SkipDailyGrader -SkipPush -NHLOnly
    if (-not $?) { throw "run_pipeline NHLOnly failed for $ds" }

    & $runPipeline -Date $ds -SkipFetch -SkipDailyGrader -SkipPush -MLBOnly
    if (-not $?) { throw "run_pipeline MLBOnly failed for $ds" }

    & $runPipeline -Date $ds -SkipFetch -SkipDailyGrader -SkipPush -SoccerOnly
    if (-not $?) { throw "run_pipeline SoccerOnly failed for $ds" }

    & $runPipeline -Date $ds -SkipDailyGrader -SkipPush -CombinedOnly
    if (-not $?) { throw "run_pipeline CombinedOnly failed for $ds" }

    & $runGrader -Date $ds
    if (-not $?) { throw "run_grader failed for $ds" }

    & py -3.14 $buildHtml --date $ds
    if (-not $?) { throw "build_grades_html failed for $ds" }

    $se = Join-Path $Root "ui_runner\templates\slate_eval_$ds.html"
    $gp = Join-Path $Root "ui_runner\templates\graded_props_$ds.json"
    $mw = Join-Path $Root "mobile\www"
    if (Test-Path -LiteralPath $se) {
        Copy-Item -LiteralPath $se -Destination (Join-Path $mw "slate_eval_$ds.html") -Force
    }
    if (Test-Path -LiteralPath $gp) {
        Copy-Item -LiteralPath $gp -Destination (Join-Path $mw "graded_props_$ds.json") -Force
    }

    Write-Host "  [done] $ds" -ForegroundColor Green
}

Write-Host ""
Write-Host "Backfill complete. Review git status; commit/push and redeploy Railway as needed." -ForegroundColor Green
