# ============================================================
# run_daily_from_git.ps1
# Intended for Task Scheduler: pull latest from origin, then run the
# pipeline with -SkipFetch (reuse on-disk / pulled inputs).
#
# Note: .gitignore ignores **/outputs/ and outputs/ — step1 CSVs under
# NBA\data\outputs\ etc. are usually NOT on GitHub unless you track them
# (exception in .gitignore) or pull them another way. Anything that *is*
# tracked (templates, JSON, committed slates) updates on pull.
#
# Usage:
#   .\scripts\run_daily_from_git.ps1
#   .\scripts\run_daily_from_git.ps1 -SkipDailyGrader
# ============================================================
param(
    [switch]$SkipDailyGrader
)

$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $PSScriptRoot
$Pipeline = Join-Path $Root "run_pipeline.ps1"

if (-not (Test-Path $Pipeline)) {
    Write-Error "run_pipeline.ps1 not found: $Pipeline"
    exit 1
}

Push-Location $Root
try {
    Write-Host ""
    Write-Host "[ GIT ] Pulling latest (fast-forward only)..." -ForegroundColor Cyan
    git pull --ff-only 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  git pull failed (exit $LASTEXITCODE). Fix the repo or network, then retry." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "  OK" -ForegroundColor Green

    $splat = @{ SkipFetch = $true }
    if ($SkipDailyGrader) { $splat.SkipDailyGrader = $true }

    Write-Host ""
    Write-Host "[ PIPELINE ] Starting run_pipeline.ps1 (SkipFetch$(if ($SkipDailyGrader) { ' + SkipDailyGrader' })) ..." -ForegroundColor Cyan
    & $Pipeline @splat
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
