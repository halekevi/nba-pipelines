# ============================================================
#  NFL PROP PIPELINE  -  Run Script
#
#  Usage:
#    .\scripts\run_nfl_pipeline.ps1
#    .\scripts\run_nfl_pipeline.ps1 -Date 2026-09-10
#    .\scripts\run_nfl_pipeline.ps1 -SkipFetch
#
#  Requires: NFL_PIPELINE_ACTIVE=1 (set below for convenience).
# ============================================================
param(
    [string]$Date = "",
    [switch]$SkipFetch,
    [int]$DefenseSeason = 2025
)

$ErrorActionPreference = "Continue"
$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) { $ScriptPath = $PSCommandPath }
$ScriptDir = Split-Path -Parent $ScriptPath
if ((Split-Path -Leaf $ScriptDir) -eq "scripts") {
    $Root = Split-Path -Parent $ScriptDir
} else {
    $Root = $ScriptDir
}
$NFLDir = Join-Path $Root "NFL"
$nflOutCanon = Join-Path $NFLDir "outputs"
if (-not (Test-Path $nflOutCanon)) {
    New-Item -ItemType Directory -Force -Path $nflOutCanon | Out-Null
}

if (-not $Date) { $Date = Get-Date -Format "yyyy-MM-dd" }

$env:NFL_PIPELINE_ACTIVE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch { }

if (Test-Path "$Root\.venv\Scripts\Activate.ps1") {
    & "$Root\.venv\Scripts\Activate.ps1"
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  NFL PIPELINE  |  $Date" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

function Run-Step {
    param([string]$Label, [string]$Dir, [string]$Script, [string]$Arguments = "")
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        if ($Arguments -and $Arguments.Trim()) {
            $argArray = $Arguments -split ' '
            $output = & py -3.14 $Script @argArray 2>&1
        } else {
            $output = & py -3.14 $Script 2>&1
        }
        $exit = $LASTEXITCODE
        $output | ForEach-Object { Write-Host "      | $_" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      FAILED (exit $exit)" -ForegroundColor Red
            return $false
        }
        Write-Host "      OK" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red
        return $false
    } finally {
        Pop-Location
    }
}

function Invoke-NFLStep7b {
    param([string]$RepoRoot)
    $p = Join-Path $RepoRoot "scripts\step7b_edge_score.py"
    if (-not (Test-Path $p)) {
        Write-Host "  [NFL] step7b: WARN missing step7b_edge_score.py" -ForegroundColor Yellow
        return
    }
    Write-Host "  --> NFL step7b (edge model)" -ForegroundColor Yellow
    Push-Location $RepoRoot
    try {
        $output = & py -3.14 $p --sport NFL 2>&1
        $exit = $LASTEXITCODE
        $output | ForEach-Object { Write-Host "      | $_" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      step7b WARN (exit $exit)" -ForegroundColor Yellow
        }
    } finally {
        Pop-Location
    }
}

$ok = $true

if (-not $SkipFetch) {
    if ($ok) { $ok = Run-Step "NFL Step 1 - Fetch PrizePicks" $NFLDir ".\scripts\step1_fetch_prizepicks_nfl.py" "--output data\outputs\step1_pp_props_today.csv --date $Date" }
} else {
    Write-Host "  [SkipFetch] Using existing step1_pp_props_today.csv" -ForegroundColor DarkGray
}

if ($ok) { $ok = Run-Step "NFL Step 2 - Clean Props" $NFLDir ".\scripts\step2_clean_props.py" "" }
if ($ok) { $ok = Run-Step "NFL Step 4 - Defense Rankings" $NFLDir ".\scripts\step4_defense_rankings.py" "--season $DefenseSeason --output data\defense_rankings.csv" }
if ($ok) { $ok = Run-Step "NFL Step 3 - Merge Defense" $NFLDir ".\scripts\step3_merge_defense_nfl.py" "" }
if ($ok) { $ok = Run-Step "NFL Step 6 - Hit Rates" $NFLDir ".\scripts\step6_historical_hit_rates.py" "" }
if ($ok) { $ok = Run-Step "NFL Step 7 - Rank Props" $NFLDir ".\scripts\step7_rank_props_nfl.py" "--output outputs\step7_nfl_ranked.xlsx" }
if ($ok) { Invoke-NFLStep7b $Root }
if ($ok) { $ok = Run-Step "NFL Step 8 - Direction Context" $NFLDir ".\scripts\step8_add_direction_context_nfl.py" "--input outputs\step7_nfl_ranked.xlsx --output outputs\step8_nfl_direction_clean.xlsx --date $Date" }

Write-Host ""
if ($ok) {
    Write-Host "  NFL pipeline complete." -ForegroundColor Green
    exit 0
}
Write-Host "  NFL pipeline FAILED." -ForegroundColor Red
exit 1
