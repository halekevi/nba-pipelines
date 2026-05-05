# ============================================================
#  WNBA GRADER  -  Fetch Actuals + Grade
#
#  Usage:
#    .\run_wnba_grader.ps1                      # Grade yesterday
#    .\run_wnba_grader.ps1 -Date 2026-07-15     # Grade specific date
# ============================================================
param(
    [string]$Date = ""
)

$ErrorActionPreference = "Continue"
$ScriptHere = $MyInvocation.MyCommand.Path
if (-not $ScriptHere) { $ScriptHere = $PSCommandPath }
$ScriptDir  = Split-Path -Parent $ScriptHere
# Repo root when this file is .../scripts/run_wnba_grader.ps1
$Root = if ((Split-Path -Leaf $ScriptDir) -eq "scripts") { Split-Path -Parent $ScriptDir } else { $ScriptDir }
$SportsRoot = Join-Path $Root "Sports"
$WNBADir = Join-Path $SportsRoot "WNBA"
$OutRoot = "$Root\outputs"

if (-not $Date) { $Date = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd") }

$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch { }

if ((Test-Path "$Root\.venv\Scripts\Activate.ps1") -and (-not $env:VIRTUAL_ENV)) {
    & "$Root\.venv\Scripts\Activate.ps1"
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  WNBA GRADER  |  $Date  |  $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

$DateDir = "$OutRoot\$Date"
if (-not (Test-Path $DateDir)) { New-Item -ItemType Directory -Force -Path $DateDir | Out-Null }

function Run-Py {
    param([string]$Label, [string]$Dir, [string]$Script, [string[]]$PyArgs)
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        $output = & py -3.14 $Script @PyArgs 2>&1
        $exit   = $LASTEXITCODE
        $output | ForEach-Object { Write-Host "      | $_" -ForegroundColor DarkGray }
        if ($exit -ne 0) { Write-Host "      FAILED (exit $exit)" -ForegroundColor Red; return $false }
        Write-Host "      OK" -ForegroundColor Green; return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red; return $false
    } finally { Pop-Location }
}

# Output paths
$WNBAActuals   = "$DateDir\actuals_wnba_$Date.csv"
$WNBATickets   = "$WNBADir\outputs\wnba_best_tickets.xlsx"
$WNBAGraded    = "$DateDir\wnba_graded_$Date.xlsx"

# =============================================================================
#  FETCH ACTUALS
# =============================================================================
Write-Host "[ WNBA FETCH ACTUALS ]" -ForegroundColor Magenta
Write-Host ""

$ok = Run-Py "WNBA Fetch Actuals" $Root ".\fetch_actuals.py" @(
    "--sport", "WNBA",
    "--date",  $Date,
    "--output",$WNBAActuals
)

# =============================================================================
#  GRADE SLATE
# =============================================================================
if ($ok -or (Test-Path $WNBAActuals)) {
    Write-Host ""
    Write-Host "[ WNBA GRADE ]" -ForegroundColor Magenta
    Write-Host ""

    if (Test-Path $WNBATickets) {
        Run-Py "WNBA Grade" $Root ".\scripts\grading\slate_grader.py" @(
            "--sport",   "WNBA",
            "--slate",   $WNBATickets,
            "--actuals", $WNBAActuals,
            "--output",  $WNBAGraded,
            "--date",    $Date
        )
    } else {
        Write-Host "  WARNING: WNBA tickets not found at $WNBATickets" -ForegroundColor Yellow
        Write-Host "  Run .\run_wnba_pipeline.ps1 first to generate tickets" -ForegroundColor Yellow
    }
} else {
    Write-Host "  Skipping grade — actuals fetch failed" -ForegroundColor Yellow
}

# =============================================================================
#  SUMMARY
# =============================================================================
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  WNBA GRADING COMPLETE  |  $Date" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

$found = Get-ChildItem $DateDir -Filter "wnba*graded*" -ErrorAction SilentlyContinue
if ($found) {
    Write-Host "  Graded outputs:" -ForegroundColor Green
    $found | ForEach-Object { Write-Host "    $($_.Name)" -ForegroundColor Green }
} else {
    Write-Host "  No WNBA graded files in $DateDir" -ForegroundColor Yellow
}
Write-Host ""
