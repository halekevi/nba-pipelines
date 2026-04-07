# Rebuild graded_props JSON for yesterday from outputs\<date>\graded_*.xlsx.
# JSON-only first; if that fails, runs build_grades_html.py for that date.

param(
    [string]$Date = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Date) {
    $Date = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
}

$Templates = Join-Path $Root "ui_runner\templates"
$JsonOnly = Join-Path $PSScriptRoot "backfill_graded_props_json.py"
$FullHtml = Join-Path $PSScriptRoot "grading\build_grades_html.py"

Push-Location $Root
try {
    Write-Host "[backfill] Date: $Date" -ForegroundColor Cyan
    if (Test-Path $JsonOnly) {
        py -3.14 $JsonOnly --date $Date --out $Templates
        if ($LASTEXITCODE -eq 0) {
            Write-Host "OK -> graded_props_$Date.json" -ForegroundColor Green
            exit 0
        }
        Write-Host "JSON-only backfill failed; trying full HTML build..." -ForegroundColor Yellow
    }
    if (-not (Test-Path $FullHtml)) {
        Write-Error "build_grades_html.py not found: $FullHtml"
        exit 1
    }
    py -3.14 $FullHtml --date $Date --out $Templates
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
