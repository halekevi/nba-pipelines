#requires -Version 5.1
<#
.SYNOPSIS
  Regenerate ui_runner/templates/slate_eval_<date>.html (and graded_props JSON) for every
  outputs folder that has graded_nba, then copy slate HTML to mobile/www.

.DESCRIPTION
  Date discovery matches only yyyy-MM-dd folder names so paths like outputs\synthetic\
  are ignored and the script exits 0 when all builds succeed.

.EXAMPLE
  .\scripts\backfill_slate_eval_from_outputs.ps1
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$dates = Get-ChildItem -Path (Join-Path $Root "outputs\*\graded_nba_*.xlsx") |
    ForEach-Object { $_.Directory.Name } |
    Where-Object { $_ -match "^\d{4}-\d{2}-\d{2}$" } |
    Sort-Object -Unique

$buildHtml = Join-Path $Root "scripts\grading\build_grades_html.py"
if (-not (Test-Path -LiteralPath $buildHtml)) {
    throw "Missing $buildHtml"
}

$i = 0
foreach ($d in $dates) {
    $i++
    Write-Host "[$i/$($dates.Count)] $d"
    & py -3.14 $buildHtml --date $d
    if ($LASTEXITCODE -ne 0) {
        throw "build_grades_html failed for $d (exit $LASTEXITCODE)"
    }
    $slate = Join-Path $Root "ui_runner\templates\slate_eval_$d.html"
    $mw = Join-Path $Root "mobile\www"
    if (Test-Path -LiteralPath $slate) {
        Copy-Item -LiteralPath $slate -Destination (Join-Path $mw "slate_eval_$d.html") -Force
    }
}

Write-Host "Backfill done ($($dates.Count) dates)."
