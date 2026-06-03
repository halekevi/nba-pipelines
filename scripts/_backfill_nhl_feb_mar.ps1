# Full NHL pipeline backfill for a historical date range (default Feb 19 – Mar 31 2026).
#
# Run from repo root:
#   pwsh -File scripts/_backfill_nhl_feb_mar.ps1
#   pwsh -File scripts/_backfill_nhl_feb_mar.ps1 -DryRun
#   pwsh -File scripts/_backfill_nhl_feb_mar.ps1 -SkipCombined
#   pwsh -File scripts/_backfill_nhl_feb_mar.ps1 -SkipFetch    # step2-8 only when step1 already on disk
#
# Entry point: run_pipeline.ps1 -NHLOnly (step1→8 under outputs/<date>/nhl/).
#   -NHLOnly is the correct sport-isolation flag (not -IncludeNHL / -Sport NHL).
#   -IncludeNHL is used by the full multi-sport parallel pipeline, not this backfill.
#
# Step1 / historical props (IMPORTANT):
#   step1_fetch_prizepicks_nhl.py --date YYYY-MM-DD hits the LIVE PrizePicks API, then
#   filters rows to that ET slate date. It does NOT restore from line_history.db or graded
#   archives. For past dates the live board is usually empty → step1 writes 0 rows and
#   steps 2-8 have nothing useful unless outputs/<date>/nhl/step1_nhl_props.csv was seeded
#   beforehand. line_history.db has 0 NHL snapshots in Feb–Mar 2026 on this machine.
#   Before a fetch-heavy run, archive or reconstruct step1 per date (or add a seed script).
#   Use -SkipFetch when step1 is already present and you only need steps 2-8 regen.
#
# Combined slate:
#   -NHLOnly normally runs combined at the end. This script passes -SkipCombined to
#   run_pipeline during the sport pass, then optionally runs -CombinedOnly once per date
#   (unless you pass -SkipCombined on this script).

param(
    [switch]$DryRun,
    [switch]$SkipCombined,
    [switch]$SkipFetch,
    [switch]$ContinueOnError = $true,
    [string]$StartDate = "2026-02-19",
    [string]$EndDate   = "2026-03-31"
)

$ErrorActionPreference = "Continue"
$Root = (Split-Path -Parent $PSScriptRoot)
$Pipeline = Join-Path $Root "run_pipeline.ps1"

if (-not (Test-Path -LiteralPath $Pipeline)) {
    Write-Host "[ERROR] Missing run_pipeline.ps1 at $Pipeline" -ForegroundColor Red
    exit 1
}

function Get-Step1RowCount {
    param([string]$CsvPath)
    if (-not (Test-Path -LiteralPath $CsvPath)) { return 0 }
    try {
        $lines = @(Get-Content -LiteralPath $CsvPath -Encoding utf8)
        if ($lines.Count -le 1) { return 0 }
        return $lines.Count - 1
    } catch {
        return 0
    }
}

$dates = @()
$cur = [datetime]::ParseExact($StartDate, "yyyy-MM-dd", $null)
$end = [datetime]::ParseExact($EndDate, "yyyy-MM-dd", $null)
while ($cur -le $end) {
    $dates += $cur.ToString("yyyy-MM-dd")
    $cur = $cur.AddDays(1)
}

$skipped = [System.Collections.Generic.List[string]]::new()
$ran = [System.Collections.Generic.List[string]]::new()
$failed = [System.Collections.Generic.List[string]]::new()
$emptyStep1 = [System.Collections.Generic.List[string]]::new()

Write-Host "=== NHL backfill $($dates.Count) dates ($StartDate .. $EndDate) ===" -ForegroundColor Yellow
Write-Host "  Repo:       $Root"
Write-Host "  DryRun:     $DryRun"
Write-Host "  SkipFetch:  $SkipFetch  (step2-8 only; requires existing step1)"
Write-Host "  Combined:   $(if ($SkipCombined) { 'off' } else { 'on (-CombinedOnly after NHL)' })"
Write-Host ""

foreach ($d in $dates) {
    $nhlDir = Join-Path $Root "outputs\$d\nhl"
    $s1 = Join-Path $nhlDir "step1_nhl_props.csv"
    if (-not (Test-Path -LiteralPath $s1)) {
        $skipped.Add($d) | Out-Null
        Write-Host "  [SKIP] $d — no step1 (unseedable)" -ForegroundColor DarkGray
        continue
    }
    $s1rows = Get-Step1RowCount -CsvPath $s1
    if ($s1rows -eq 0) {
        $skipped.Add($d) | Out-Null
        Write-Host "  [SKIP] $d — step1 empty (0 rows)" -ForegroundColor DarkGray
        continue
    }

    $s8 = Join-Path $nhlDir "step8_nhl_direction_clean.xlsx"
    if (Test-Path -LiteralPath $s8) {
        $skipped.Add($d) | Out-Null
        Write-Host "  [SKIP] $d — step8 exists" -ForegroundColor DarkGray
        continue
    }

    Write-Host ""
    Write-Host "  [RUN] $d" -ForegroundColor Cyan
    if ($DryRun) {
        $ran.Add($d) | Out-Null
        continue
    }

    $pipeArgs = @("-File", $Pipeline, "-Date", $d, "-NHLOnly", "-SkipCombined")
    if ($SkipFetch) { $pipeArgs += "-SkipFetch" }

    & pwsh @pipeArgs
    if ($LASTEXITCODE -ne 0) {
        $failed.Add($d) | Out-Null
        Write-Host "  [FAIL] $d — run_pipeline -NHLOnly exit $LASTEXITCODE" -ForegroundColor Red
        if (-not $ContinueOnError) { break }
        continue
    }

    $s1 = Join-Path $nhlDir "step1_nhl_props.csv"
    $n1 = Get-Step1RowCount -CsvPath $s1
    if ($n1 -le 0) {
        $emptyStep1.Add($d) | Out-Null
        Write-Host "  [WARN] $d — step1 has 0 rows (live PP fetch cannot backfill historical boards)" -ForegroundColor Yellow
    } else {
        Write-Host "  [OK]   $d — step1 rows=$n1" -ForegroundColor Green
    }

    if (-not (Test-Path -LiteralPath $s8)) {
        $failed.Add($d) | Out-Null
        Write-Host "  [FAIL] $d — step8 missing after pipeline" -ForegroundColor Red
        if (-not $ContinueOnError) { break }
        continue
    }

    if (-not $SkipCombined) {
        & pwsh -File $Pipeline -Date $d -CombinedOnly
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [WARN] $d — CombinedOnly exit $LASTEXITCODE" -ForegroundColor Yellow
        }
    }

    $ran.Add($d) | Out-Null
}

Write-Host ""
Write-Host "=== Backfill summary ===" -ForegroundColor Yellow
Write-Host "  Dates in range: $($dates.Count)"
Write-Host "  Ran:            $($ran.Count)"
Write-Host "  Skipped:        $($skipped.Count)"
Write-Host "  Failed:         $($failed.Count)$(if ($failed.Count) { " — $($failed -join ', ')" } else { '' })"
Write-Host "  Empty step1:    $($emptyStep1.Count)$(if ($emptyStep1.Count) { " — $($emptyStep1 -join ', ')" } else { '' })"

if ($failed.Count -gt 0) { exit 1 }
exit 0
