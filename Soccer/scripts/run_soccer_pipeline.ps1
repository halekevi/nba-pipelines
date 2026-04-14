# run_soccer_pipeline.ps1  -  PropOracle Soccer Pipeline
#
# Usage (run from Soccer\ root):
#   .\scripts\run_soccer_pipeline.ps1
#   .\scripts\run_soccer_pipeline.ps1 -SkipFetch
#   .\scripts\run_soccer_pipeline.ps1 -LeagueId 1234
#   .\scripts\run_soccer_pipeline.ps1 -NTeams 20
#   .\scripts\run_soccer_pipeline.ps1 -Date 2026-03-21   # grader copy targets outputs\<Date>\

param(
    [switch]$SkipFetch,
    [string]$LeagueId = "",
    [int]$NTeams      = 15,
    [string]$Date     = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
$script:ProgressDone = 0
$script:ProgressTotal = if ($SkipFetch) { 7 } else { 8 }

# ── Resolve paths ─────────────────────────────────────────────────────────────
# Support running from Soccer\ root OR from Soccer\scripts\
$ScriptDir   = $PSScriptRoot
$SoccerRoot  = if ((Split-Path $ScriptDir -Leaf) -eq "scripts") { Split-Path $ScriptDir -Parent } else { $ScriptDir }
$ScriptsDir  = Join-Path $SoccerRoot "scripts"
$OutputsDir  = Join-Path $SoccerRoot "outputs"
$CacheDir    = Join-Path $SoccerRoot "cache"
$RepoRoot    = Split-Path $SoccerRoot -Parent

if (-not $Date) { $Date = (Get-Date -Format "yyyy-MM-dd") }

if (-not (Test-Path $OutputsDir)) { New-Item -ItemType Directory -Path $OutputsDir -Force | Out-Null }

function Run-Step {
    param([string]$Label, [string]$Script, [string[]]$StepArgs)
    $tag     = "[ PropOracle-Soccer-$Label ]"
    $fullPath = Join-Path $ScriptsDir $Script
    Write-Host ""
    Write-Host "$tag Starting..." -ForegroundColor Cyan
    Write-Host "        CMD: py -3.14 `"$fullPath`" $($StepArgs -join ' ')" -ForegroundColor DarkGray
    & py -3.14 $fullPath @StepArgs
    if ($LASTEXITCODE -ne 0) {
        $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
        $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
        Write-Progress -Id 3 -Activity "Soccer Pipeline" -Status "$Label [FAILED] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
        Write-Host "$tag FAILED (exit $LASTEXITCODE) - aborting." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
    $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
    Write-Progress -Id 3 -Activity "Soccer Pipeline" -Status "$Label [OK] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
    Write-Host "$tag OK" -ForegroundColor Green
}

Set-Location $SoccerRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  PropOracle Soccer Pipeline" -ForegroundColor Cyan
Write-Host "  Root: $SoccerRoot" -ForegroundColor DarkGray
Write-Host "========================================" -ForegroundColor Cyan
Write-Progress -Id 3 -Activity "Soccer Pipeline" -Status "Starting..." -PercentComplete 0

# NOTE: Soccer reference data must be refreshed manually via:
#   py -3.14 scripts\build_fbref_soccer_ref.py --list-leagues
# Save FBref HTML files to data\cache\fbref_html\ first.

# ── S1: Fetch PrizePicks ──────────────────────────────────────────────────────
if ($SkipFetch) {
    Write-Host ""
    Write-Host "[ PropOracle-Soccer-S1 ] SKIPPED (--SkipFetch)" -ForegroundColor Yellow
    if (-not (Test-Path (Join-Path $OutputsDir "step1_soccer_props.csv"))) {
        Write-Host "[ PropOracle-Soccer-S1 ] ERROR: outputs\step1_soccer_props.csv not found." -ForegroundColor Red
        exit 1
    }
} else {
    $s1args = [System.Collections.Generic.List[string]]@("--output", "$OutputsDir\step1_soccer_props.csv")
    if ($LeagueId -ne "") { $s1args.Add("--league_id"); $s1args.Add($LeagueId) }
    Run-Step "S1" "step1_fetch_prizepicks_soccer.py" $s1args.ToArray()
}

# ── S2: Attach Pick Types + ESPN IDs ─────────────────────────────────────────
Run-Step "S2" "step2_attach_picktypes_soccer.py" @(
    "--input",       "$OutputsDir\step1_soccer_props.csv",
    "--output",      "$OutputsDir\step2_soccer_picktypes.csv",
    "--idcache",     "$CacheDir\soccer_espn_id_cache.csv",
    "--rostercache", "$CacheDir\soccer_roster_cache.csv"
)

# ── S3: Attach Defense ────────────────────────────────────────────────────────
$DefenseCsv = Join-Path $CacheDir "soccer_defense_summary.csv"
if (Test-Path $DefenseCsv) {
    Run-Step "S3" "step3_attach_defense_soccer.py" @(
        "--input",   "$OutputsDir\step2_soccer_picktypes.csv",
        "--defense", $DefenseCsv,
        "--output",  "$OutputsDir\step3_soccer_with_defense.csv"
    )
} else {
    Write-Host ""
    Write-Host "[ PropOracle-Soccer-S3 ] Soccer defense CSV missing — defense context will be skipped" -ForegroundColor Yellow
    Write-Host "        (expected: $DefenseCsv)" -ForegroundColor DarkGray
    Copy-Item (Join-Path $OutputsDir "step2_soccer_picktypes.csv") (Join-Path $OutputsDir "step3_soccer_with_defense.csv") -Force
    $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
    $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
    Write-Progress -Id 3 -Activity "Soccer Pipeline" -Status "S3 [SKIPPED] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
    Write-Host "[ PropOracle-Soccer-S3 ] OK (passthrough from S2)" -ForegroundColor Green
}

# ── S4: Attach Player Stats ───────────────────────────────────────────────────
Run-Step "S4" "step4_attach_player_stats_soccer.py" @(
    "--input",   "$OutputsDir\step3_soccer_with_defense.csv",
    "--cache",   "$CacheDir\soccer_stats_cache.csv",
    "--output",  "$OutputsDir\step4_soccer_with_stats.csv",
    "--workers", "6"
)

# ── S5: Line Hit Rates (L5 + L10) ─────────────────────────────────────────────
# --compute10 is now default=True in the script, but passed explicitly here
# to be unambiguous and forward-compatible.
Run-Step "S5" "step5_add_line_hit_rates_soccer.py" @(
    "--input",     "$OutputsDir\step4_soccer_with_stats.csv",
    "--output",    "$OutputsDir\step5_soccer_hit_rates.csv",
    "--compute10"
)

# ── S6: Team Role Context ─────────────────────────────────────────────────────
Run-Step "S6" "step6_team_role_context_soccer.py" @(
    "--input",  "$OutputsDir\step5_soccer_hit_rates.csv",
    "--output", "$OutputsDir\step6_soccer_role_context.csv"
)

# ── S7: Rank Props ────────────────────────────────────────────────────────────
Run-Step "S7" "step7_rank_props_soccer.py" @(
    "--input",   "$OutputsDir\step6_soccer_role_context.csv",
    "--output",  "$OutputsDir\step7_soccer_ranked.xlsx",
    "--n_teams", "$NTeams"
)

# ── S7b: Unified edge score overlay (ml_prob / edge_score / blended_score) ──
$Step7bScript = Join-Path $RepoRoot "scripts\step7b_edge_score.py"
if (Test-Path $Step7bScript) {
    Write-Host ""
    Write-Host "[ PropOracle-Soccer-S7b ] Starting..." -ForegroundColor Cyan
    & py -3.14 $Step7bScript --sport Soccer --step7-xlsx "$OutputsDir\step7_soccer_ranked.xlsx" --repo-root "$RepoRoot"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ PropOracle-Soccer-S7b ] FAILED (exit $LASTEXITCODE) - aborting." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "[ PropOracle-Soccer-S7b ] OK" -ForegroundColor Green
} else {
    Write-Host "[ PropOracle-Soccer-S7b ] SKIPPED (script not found: $Step7bScript)" -ForegroundColor Yellow
}

# ── S8: Direction Context + Clean XLSX ───────────────────────────────────────
# --xlsx produces the clean formatted workbook (step8_soccer_direction_clean.xlsx)
# which is what run_pipeline.ps1 and combined_slate_tickets.py consume.
Run-Step "S8" "step8_add_direction_context_soccer.py" @(
    "--input",  "$OutputsDir\step7_soccer_ranked.xlsx",
    "--sheet",  "ALL",
    "--output", "$OutputsDir\step8_soccer_direction.csv",
    "--xlsx",   "$OutputsDir\step8_soccer_direction_clean.xlsx"
)

# ── S8b: Direction / edge health checks (non-blocking warnings) ──────────────
Run-Step "S8b" "healthcheck_soccer_directions.py" @(
    "--step7", "$OutputsDir\step7_soccer_ranked.xlsx",
    "--step8", "$OutputsDir\step8_soccer_direction_clean.xlsx"
)

# Copy clean slate into repo outputs\<Date>\ for run_grader.ps1 (dated filename).
$DateDir = Join-Path $RepoRoot "outputs\$Date"
if (-not (Test-Path $DateDir)) { New-Item -ItemType Directory -Path $DateDir -Force | Out-Null }
$Step8Clean = Join-Path $OutputsDir "step8_soccer_direction_clean.xlsx"
$Step8Dated = Join-Path $DateDir "step8_soccer_direction_clean_$Date.xlsx"
if (Test-Path $Step8Clean) {
    Copy-Item $Step8Clean $Step8Dated -Force
    Write-Host "[ PropOracle-Soccer ] Copied step8 workbook for grader: $Step8Dated" -ForegroundColor DarkGray
} else {
    Write-Host "[ PropOracle-Soccer ] WARN: step8 clean xlsx not found — skip grader copy ($Step8Clean)" -ForegroundColor Yellow
}

# ── S9 DISABLED ───────────────────────────────────────────────────────────────
# Tickets are generated by combined_slate_tickets.py in run_pipeline.ps1
# Re-enable with: Run-Step "S9" "step9_build_tickets_soccer.py" @(...)

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  PropOracle Soccer Pipeline COMPLETE" -ForegroundColor Green
Write-Host "  $OutputsDir\step7_soccer_ranked.xlsx" -ForegroundColor Green
Write-Host "  $OutputsDir\step8_soccer_direction_clean.xlsx" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Progress -Id 3 -Activity "Soccer Pipeline" -Completed
