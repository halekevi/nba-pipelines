# Emit ui_runner/templates/graded_props_YYYY-MM-DD.json for yesterday (or -Date).
# Requires graded_*.xlsx under outputs\<date>\ (run run_grader.ps1 first if missing).
param([string]$Date = "")

$Root = Split-Path -Parent $PSScriptRoot
if (-not $Date) {
    $Date = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
}
Set-Location $Root
Write-Host "Backfill graded_props JSON for slate date: $Date" -ForegroundColor Cyan

$bf = Join-Path $Root "scripts\backfill_graded_props_json.py"
$bg = Join-Path $Root "scripts\grading\build_grades_html.py"
$out = Join-Path $Root "ui_runner\templates"

function Run-Py {
    param([string[]]$PyArgs)
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @PyArgs
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @PyArgs
    } else {
        Write-Error "Python not found."
        exit 1
    }
}

# Fast path: JSON only (needs build_grades_html.py with export_graded_props_json).
if ((Test-Path $bf) -and (Test-Path $bg)) {
    Run-Py @($bf, "--date", $Date)
    if ($LASTEXITCODE -eq 0) { exit 0 }
    Write-Host "JSON-only backfill failed; running full slate HTML + JSON build..." -ForegroundColor Yellow
}

if (-not (Test-Path $bg)) {
    Write-Error "Missing $bg"
    exit 1
}

Run-Py @($bg, "--date", $Date, "--out", $out)
exit $LASTEXITCODE
