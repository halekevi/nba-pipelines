# ============================================================
#  fix_encoding.ps1
#  One-time script to re-save all intermediate CSVs as UTF-8
#  so Unicode player names (Jokić, Vučević, Dëmin) survive
#  the full pipeline without corruption.
#  Run once after dropping the new step4 in place.
# ============================================================

$Root   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$NBADir = "$Root\NbaPropPipelineA"

Write-Host ""
Write-Host "[ FIXING CSV ENCODING -> UTF-8 ]" -ForegroundColor Cyan
Write-Host ""

$csvFiles = Get-ChildItem "$NBADir\*.csv" -ErrorAction SilentlyContinue

foreach ($f in $csvFiles) {
    try {
        # Read with default encoding detection, re-save as UTF-8 with BOM
        $content = Get-Content $f.FullName -Encoding Default
        $content | Out-File $f.FullName -Encoding utf8 -Force
        Write-Host "  Fixed: $($f.Name)" -ForegroundColor Green
    } catch {
        Write-Host "  Skipped: $($f.Name) - $_" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Now re-run the pipeline:" -ForegroundColor Cyan
Write-Host "  .\run_pipeline.ps1 -NBAOnly" -ForegroundColor White
Write-Host ""
