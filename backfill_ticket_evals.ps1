#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
  Backfill ticket_eval_*.html from outputs\*\combined_slate_tickets_*.xlsx via build_ticket_eval.py.
#>

$ErrorActionPreference = 'Continue'

$Root = $PSScriptRoot
$outputsDir = Join-Path $Root 'outputs'
$templatesDir = Join-Path $Root 'ui_runner/templates'
$dateRx = [regex]::new(
    '^combined_slate_tickets_(?<d>\d{4}-\d{2}-\d{2})(?:_.*)?\.xlsx$',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

function Invoke-TicketEvalGit {
    Set-Location -LiteralPath $Root

    if (-not (Test-Path -LiteralPath $templatesDir)) {
        Write-Host "[INFO] templates folder missing; skipping git steps" -ForegroundColor DarkGray
        return
    }
    # ticket_eval_latest.html matches ticket_eval_*.html
    Get-ChildItem -LiteralPath $templatesDir -Filter 'ticket_eval_*.html' -File -ErrorAction SilentlyContinue |
        ForEach-Object { git add -- $_.FullName }

    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[INFO] No staged ticket_eval HTML changes; skipping commit/push" -ForegroundColor DarkGray
        return
    }

    git commit -m "backfill: ticket evals with correct dated legs"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] git commit failed; push skipped" -ForegroundColor Yellow
        return
    }

    git push
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] push failed — commit is local, push manually" -ForegroundColor Yellow
    } else {
        Write-Host "[INFO] Commit and push completed." -ForegroundColor Green
    }
}

if (-not (Test-Path -LiteralPath $outputsDir)) {
    Write-Host "[ERROR] outputs folder not found: $outputsDir" -ForegroundColor Red
    exit 1
}

$all = @(
    Get-ChildItem -LiteralPath $outputsDir -Recurse -File -Filter 'combined_slate_tickets_*.xlsx' |
        Sort-Object -Property FullName
)

if ($all.Count -eq 0) {
    Write-Host "[WARN] No files matched outputs\*\combined_slate_tickets_*.xlsx" -ForegroundColor Yellow
    Write-Host "[DONE] 0 succeeded, 0 failed"
    Invoke-TicketEvalGit
    exit 0
}

$success = 0
$failed = 0
$n = $all.Count
$idx = 0

foreach ($f in $all) {
    $idx++
    $m = $dateRx.Match($f.Name)
    if (-not $m.Success) {
        Write-Host "[$idx/$n] [SKIP] $($f.Name) (no YYYY-MM-DD in filename)" -ForegroundColor DarkYellow
        continue
    }
    $date = $m.Groups['d'].Value
    $canonical = Join-Path $Root "combined_slate_tickets_$date.xlsx"

    Write-Host ""
    Write-Host "[$idx/$n] $date  ←  $($f.FullName)" -ForegroundColor Cyan

    try {
        Copy-Item -LiteralPath $f.FullName -Destination $canonical -Force
    } catch {
        Write-Host "[FAIL] $date  (copy to repo root failed: $_)" -ForegroundColor Red
        $failed++
        continue
    }

    & py -3.14 (Join-Path $Root 'build_ticket_eval.py') --date $date
    $exitCode = $LASTEXITCODE

    Remove-Item -LiteralPath $canonical -Force -ErrorAction SilentlyContinue

    if ($exitCode -ne 0) {
        Write-Host "[FAIL] $date" -ForegroundColor Red
        $failed++
    } else {
        Write-Host "[OK]   $date" -ForegroundColor Green
        $success++
    }
}

Write-Host ""
Write-Host "[DONE] $success succeeded, $failed failed"

Invoke-TicketEvalGit
