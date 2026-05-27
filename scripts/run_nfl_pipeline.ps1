# NFL Pipeline — SCAFFOLD ONLY
# Activate: remove BOARD_SIZE_MIN=0 gates in step1, set
#           NFL_PARALLEL_ACTIVE check in run_pipeline.ps1
# Target:   Week 1 2026 season (September 2026)
param(
    [string]$Date = "",
    [switch]$SkipFetch
)

$ErrorActionPreference = "Continue"
$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) { $ScriptPath = $PSCommandPath }
$ScriptDir = Split-Path -Parent $ScriptPath
$Root = Split-Path -Parent $ScriptDir
$NFLDir = Join-Path $Root "Sports\NFL"

if (-not $Date) { $Date = Get-Date -Format "yyyy-MM-dd" }
$OutDir = Join-Path $Root "outputs\$Date\nfl"
$SportOutDir = Join-Path $NFLDir "outputs"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
if (-not (Test-Path $SportOutDir)) { New-Item -ItemType Directory -Force -Path $SportOutDir | Out-Null }

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
Write-Host "  NFL PIPELINE SCAFFOLD  |  $Date  |  $OutDir" -ForegroundColor Cyan
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

function Get-CsvDataRowCount([string]$CsvPath) {
    if (-not (Test-Path -LiteralPath $CsvPath)) { return 0 }
    try {
        $raw = Import-Csv -LiteralPath $CsvPath
        if ($null -eq $raw) { return 0 }
        if ($raw -is [array]) { return $raw.Count }
        return 1
    } catch {
        return 0
    }
}

$s1 = Join-Path $OutDir "step1_nfl_props.csv"
$s2 = Join-Path $OutDir "step2_nfl_player_ids.csv"
$s3 = Join-Path $OutDir "step3_nfl_defense.csv"
$s4 = Join-Path $OutDir "step4_nfl_player_stats.csv"
$s5 = Join-Path $OutDir "step5_nfl_hit_rates.csv"
$s6 = Join-Path $OutDir "step6b_nfl_game_context.csv"
$s7 = Join-Path $OutDir "step7_nfl_ranked.xlsx"
$s8 = Join-Path $OutDir "step8_nfl_direction_clean.xlsx"

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

$step1Rows = Get-CsvDataRowCount -CsvPath $s1
if ($ok -and $step1Rows -eq 0) {
    Write-Host "[NFL] Off-season — no board for $Date. Scaffold only."
    exit 0
}

if ($ok) { $ok = Run-Step "NFL Step 2 - Attach Player IDs" $NFLDir ".\scripts\step2_attach_player_ids_nfl.py" "--input $s1 --output $s2" }
if ($ok) { $ok = Run-Step "NFL Step 3 - Attach Defense" $NFLDir ".\scripts\step3_attach_defense_nfl.py" "--input $s2 --output $s3" }
if ($ok) { $ok = Run-Step "NFL Step 4 - Attach Player Stats" $NFLDir ".\scripts\step4_attach_player_stats_espn_cache_nfl.py" "--input $s3 --output $s4" }
if ($ok) { $ok = Run-Step "NFL Step 5 - Attach Hit Rates" $NFLDir ".\scripts\step5_attach_hit_rates_nfl.py" "--input $s4 --output $s5" }
if ($ok) { $ok = Run-Step "NFL Step 6b - Attach Game Context" $NFLDir ".\scripts\step6b_attach_game_context_nfl.py" "--input $s5 --output $s6" }
if ($ok) { $ok = Run-Step "NFL Step 7 - Rank Props" $NFLDir ".\scripts\step7_rank_props_nfl.py" "--input $s6 --output $s7" }
if ($ok) {
    $ok = Run-Step "NFL Step 8 - Direction Context" $NFLDir ".\scripts\step8_add_direction_context_nfl.py" "--input $s7 --output $s8 --date $Date"
}

if ($ok -and (Test-Path -LiteralPath $s8)) {
    Copy-Item -LiteralPath $s8 -Destination (Join-Path $SportOutDir "step8_nfl_direction_clean.xlsx") -Force
    Copy-Item -LiteralPath $s8 -Destination (Join-Path $OutDir "step8_nfl_direction_clean.xlsx") -Force
}

Write-Host ""
if ($ok) {
    Write-Host "  NFL pipeline complete -> $s8" -ForegroundColor Green
    exit 0
}
Write-Host "  NFL pipeline FAILED." -ForegroundColor Red
exit 1
