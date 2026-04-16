#requires -Version 5.1
param()

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$Grader = Join-Path $Root "scripts\run_grader.ps1"

if (-not (Test-Path $Grader)) {
    Write-Error "Missing grader script: $Grader"
    exit 1
}

Set-Location $Root
Write-Host "[5AM GRADER] Pulling latest repository..." -ForegroundColor Cyan
git pull --ff-only 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
    Write-Host "[5AM GRADER] git pull failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

$gradeDate = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
Write-Host "[5AM GRADER] Running run_grader.ps1 -Date $gradeDate" -ForegroundColor Cyan
& pwsh -NoProfile -File $Grader -Date $gradeDate
if ($LASTEXITCODE -ne 0) {
    Write-Host "[5AM GRADER] run_grader failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[5AM GRADER] Complete" -ForegroundColor Green
exit 0
