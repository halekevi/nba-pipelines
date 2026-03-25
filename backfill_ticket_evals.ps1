#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
  Rebuild ticket_eval_YYYY-MM-DD.html for every date that has a combined_slate_tickets workbook.

.DESCRIPTION
  Uses build_ticket_eval.py date mode directly (no temporary root copies, no auto git commit/push).
  Date discovery source: outputs\**\combined_slate_tickets_*.xlsx
#>

param(
    [string]$Date = "",
    [switch]$KeepLatest
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$OutputsDir = Join-Path $Root "outputs"
$BuildScript = Join-Path $Root "build_ticket_eval.py"
$dateRx = [regex]::new("combined_slate_tickets_(?<d>\d{4}-\d{2}-\d{2})", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)

if (-not (Test-Path -LiteralPath $BuildScript)) {
    Write-Host "[ERROR] Missing build script: $BuildScript" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $OutputsDir)) {
    Write-Host "[ERROR] outputs folder not found: $OutputsDir" -ForegroundColor Red
    exit 1
}

$targetDates = @()
if ($Date) {
    $targetDates = @($Date)
}
else {
    $files = Get-ChildItem -LiteralPath $OutputsDir -Recurse -File -Filter "combined_slate_tickets_*.xlsx" -ErrorAction SilentlyContinue
    $targetDates = @(
        $files |
        ForEach-Object {
            $m = $dateRx.Match($_.Name)
            if ($m.Success) { $m.Groups["d"].Value }
        } |
        Where-Object { $_ -match '^\d{4}-\d{2}-\d{2}$' } |
        Sort-Object -Unique
    )
}

if ($targetDates.Count -eq 0) {
    Write-Host "[WARN] No backfill dates found." -ForegroundColor Yellow
    exit 0
}

$latestDate = ($targetDates | Sort-Object)[-1]
$success = 0
$failed = 0
$n = $targetDates.Count
$idx = 0
$templatesDir = Join-Path $Root "ui_runner/templates"
$latestPath = Join-Path $templatesDir "tickets_latest.html"
$savedLatest = $null
if ($KeepLatest -and (Test-Path -LiteralPath $latestPath)) {
    try {
        $savedLatest = Get-Content -LiteralPath $latestPath -Raw -ErrorAction Stop
    } catch {
        $savedLatest = $null
    }
}

foreach ($d in $targetDates) {
    $idx++
    Write-Host ""
    Write-Host "[$idx/$n] Rebuilding ticket eval for $d" -ForegroundColor Cyan
    & py -3.14 $BuildScript --date $d
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $d" -ForegroundColor Red
        $failed++
        continue
    }
    Write-Host "[OK]   $d" -ForegroundColor Green
    $success++
}

# Preserve current tickets_latest.html if requested.
if ($KeepLatest -and $savedLatest -ne $null) {
    try {
        Set-Content -LiteralPath $latestPath -Value $savedLatest -Encoding UTF8
        Write-Host "[INFO] Restored previous tickets_latest.html (--KeepLatest)." -ForegroundColor DarkGray
    } catch {
        Write-Host "[WARN] Could not restore prior tickets_latest.html" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[DONE] $success succeeded, $failed failed"
Write-Host "[INFO] Review diffs and commit/push manually."
