# ============================================================
# daily_grades.ps1
# Builds yesterday's grade HTML and pushes to GitHub.
# Run this every morning after graders have finished.
#
# Usage:
#   .\daily_grades.ps1              # uses yesterday
#   .\daily_grades.ps1 -Date 2026-03-06  # override date
# ============================================================

param(
    [string]$Date = ""
)

# Resolve repo root from this script location so path changes don't break runs.
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$GRADING = Join-Path $ROOT "scripts\grading"
$TEMPLATES = Join-Path $ROOT "ui_runner\templates"

if ($Date -eq "") {
    $Date = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  PropOracle Daily Grades — $Date" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ── Step 1: Build HTML ────────────────────────────────────────
Write-Host "`n[1/2] Building HTML ..." -ForegroundColor Yellow
Set-Location $ROOT
py -3.14 "$GRADING\build_grades_html.py" --date $Date --out $TEMPLATES

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n  ERROR: HTML build failed. Check that graded files exist in:" -ForegroundColor Red
    Write-Host "  $(Join-Path $ROOT "outputs\$Date\")" -ForegroundColor Red
    exit 1
}

# ── Step 2: Push to GitHub ────────────────────────────────────
Write-Host "`n[2/2] Pushing to GitHub ..." -ForegroundColor Yellow
Set-Location $ROOT

git add "ui_runner/templates/slate_eval_$Date.html"
git commit -m "Grades $Date"
git push origin main

Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  Done! $Date grades are live." -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
