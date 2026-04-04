#requires -Version 7.2
<#
.SYNOPSIS
  Copy graded_*.xlsx from outputs\<date>\ into ui_runner\graded_slate\<date>\ for git commit.
  Railway serves tickets_latest.html from the repo; outputs/ is gitignored, so MLB (etc.) grades
  need this bundle (or the live page stays VOID for those legs).
.EXAMPLE
  .\scripts\stage_graded_slate_for_deploy.ps1 -Date 2026-04-02
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Date
)

$Root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $Root "outputs\$Date"
$dst = Join-Path $Root "ui_runner\graded_slate\$Date"

if (-not (Test-Path $src)) {
    Write-Error "Missing folder: $src"
    exit 1
}

$files = Get-ChildItem -LiteralPath $src -File -Filter "graded_*.xlsx" -ErrorAction SilentlyContinue
if (-not $files -or $files.Count -eq 0) {
    Write-Warning "No graded_*.xlsx in $src — run run_grader.ps1 or mlb_grade_date.py first."
    exit 1
}

New-Item -ItemType Directory -Path $dst -Force | Out-Null
foreach ($f in $files) {
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $dst $f.Name) -Force
    Write-Host "  -> $($f.Name)"
}

Write-Host "Staged $($files.Count) file(s) under ui_runner\graded_slate\$Date"
Write-Host "Next: py -3.14 scripts\build_ticket_eval.py --date $Date"
Write-Host "Then: git add ui_runner/graded_slate/$Date ui_runner/templates/tickets_latest.html ..."
