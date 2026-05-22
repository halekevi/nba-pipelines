# ============================================================
#  NFL PROP PIPELINE  -  Run Script
#
#  Usage:
#    .\scripts\run_nfl_pipeline.ps1
#    .\scripts\run_nfl_pipeline.ps1 -Date 2026-05-18 -OutDir outputs\2026-05-18\nfl
#    .\scripts\run_nfl_pipeline.ps1 -SkipFetch
#
#  Requires: NFL_PIPELINE_ACTIVE=1 (set below for convenience).
# ============================================================
param(
    [string]$Date = "",
    [string]$OutDir = "",
    [switch]$SkipFetch,
    [int]$DefenseSeason = 2025
)

$ErrorActionPreference = "Continue"
$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) { $ScriptPath = $PSCommandPath }
$ScriptDir = Split-Path -Parent $ScriptPath
$Root = Split-Path -Parent $ScriptDir
$SportsRoot = Join-Path $Root "Sports"
$NFLDir = Join-Path $SportsRoot "NFL"

if (-not $Date) { $Date = Get-Date -Format "yyyy-MM-dd" }
if (-not $OutDir) {
    $OutDir = Join-Path $Root "outputs" $Date "nfl"
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = Join-Path $Root $OutDir
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

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
Write-Host "  NFL PIPELINE  |  $Date  |  $OutDir" -ForegroundColor Cyan
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
    param([string]$RepoRoot, [string]$Step7Xlsx = "")
    $p = Join-Path $RepoRoot "scripts\step7b_edge_score.py"
    if (-not (Test-Path $p)) {
        Write-Host "  [NFL] step7b: WARN missing step7b_edge_score.py" -ForegroundColor Yellow
        return
    }
    Write-Host "  --> NFL step7b (edge model)" -ForegroundColor Yellow
    Push-Location $RepoRoot
    try {
        $step7Args = @("--sport", "NFL")
        if ($Step7Xlsx -ne "") { $step7Args += @("--step7-xlsx", $Step7Xlsx) }
        $output = & py -3.14 $p @step7Args 2>&1
        $exit = $LASTEXITCODE
        $output | ForEach-Object { Write-Host "      | $_" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      step7b WARN (exit $exit)" -ForegroundColor Yellow
        }
    } finally {
        Pop-Location
    }
}

$s1 = Join-Path $OutDir "step1_pp_props_today.csv"
$s2 = Join-Path $OutDir "step2_clean_props.csv"
$s3 = Join-Path $OutDir "step3_with_defense.csv"
$s6 = Join-Path $OutDir "step6_hit_rates.csv"
$s7 = Join-Path $OutDir "step7_nfl_ranked.xlsx"
$s8 = Join-Path $OutDir "step8_nfl_direction_clean.xlsx"
$defCsv = Join-Path $NFLDir "data\defense_rankings.csv"
$last5 = Join-Path $OutDir "nfl_team_last5.csv"

$ok = $true

if (-not $SkipFetch) {
    if ($ok) {
        $ok = Run-Step "NFL Step 1 - Fetch PrizePicks" $NFLDir ".\scripts\step1_fetch_prizepicks_nfl.py" "--output $s1 --date $Date"
    }
} else {
    Write-Host "  [SkipFetch] Using existing $s1" -ForegroundColor DarkGray
    if (-not (Test-Path $s1)) {
        Write-Host "  ERROR: SkipFetch but missing $s1" -ForegroundColor Red
        $ok = $false
    }
}

if ($ok) { $ok = Run-Step "NFL Step 2 - Clean Props" $NFLDir ".\scripts\step2_clean_props.py" "--input $s1 --output $s2" }
if ($ok) { $ok = Run-Step "NFL Step 4 - Defense Rankings" $NFLDir ".\scripts\step4_defense_rankings.py" "--season $DefenseSeason --output $defCsv" }
if ($ok) {
    $ok = Run-Step "NFL Step 4b - Team last-5 games (ESPN)" $NFLDir ".\scripts\step4b_team_last5_games.py" "--season $DefenseSeason --output $last5"
}
if ($ok) {
    $ok = Run-Step "NFL Step 3 - Merge Defense" $NFLDir ".\scripts\step3_merge_defense_nfl.py" "--input $s2 --defense $defCsv --team-form $last5 --output $s3"
}
if ($ok) {
    $ok = Run-Step "NFL Step 4c - Role context (stub)" $NFLDir ".\scripts\step4c_attach_role_context_nfl.py" "--input $s3 --output $s3"
}
if ($ok) { $ok = Run-Step "NFL Step 6 - Hit Rates" $NFLDir ".\scripts\step6_historical_hit_rates.py" "--input $s3 --output $s6" }
if ($ok) { $ok = Run-Step "NFL Step 7 - Rank Props" $NFLDir ".\scripts\step7_rank_props_nfl.py" "--input $s6 --output $s7" }
if ($ok) { Invoke-NFLStep7b $Root $s7 }
if ($ok) {
    $ok = Run-Step "NFL Step 8 - Direction Context" $NFLDir ".\scripts\step8_add_direction_context_nfl.py" "--input $s7 --output $s8 --date $Date"
}

Write-Host ""
if ($ok) {
    Write-Host "  NFL pipeline complete -> $s8" -ForegroundColor Green
    exit 0
}
Write-Host "  NFL pipeline FAILED." -ForegroundColor Red
exit 1
