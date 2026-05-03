# ============================================================
#  WNBA PROP PIPELINE  -  Run Script
#
#  Usage:
#    .\run_wnba_pipeline.ps1              # Full WNBA pipeline run
#    .\run_wnba_pipeline.ps1 -Date 2026-07-15   # Specify date
#    .\run_wnba_pipeline.ps1 -RefreshCache      # Wipe ESPN cache + rebuild
#    .\run_wnba_pipeline.ps1 -SkipFetch         # Use existing step1 output
#    .\run_wnba_pipeline.ps1 -Cdp http://127.0.0.1:9222   # Chrome CDP after DataDome solve
#  Env (optional): PROPORACLE_PP_CDP or PRIZEPICKS_CDP — same as -Cdp when -Cdp omitted.
# ============================================================
param(
    [string]$Date         = "",
    [switch]$RefreshCache,
    [switch]$SkipFetch,
    [string]$Cdp          = ""
)

$ErrorActionPreference = "Continue"
# Repo root when this file lives under ...\scripts\; otherwise treat script directory as root.
$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) { $ScriptPath = $PSCommandPath }
$ScriptDir  = Split-Path -Parent $ScriptPath
if ((Split-Path -Leaf $ScriptDir) -eq "scripts") {
    $Root = Split-Path -Parent $ScriptDir
} else {
    $Root = $ScriptDir
}
# Canonical sport tree (matches run_pipeline.ps1 $SportsRoot\WNBA).
$WNBADir = Join-Path $Root "Sports\WNBA"
$OutRoot = Join-Path $Root "outputs"

if (-not $Date) { $Date = Get-Date -Format "yyyy-MM-dd" }
$Cdp = $Cdp.Trim()
if (-not $Cdp) { $Cdp = [string]$env:PROPORACLE_PP_CDP }
if (-not $Cdp) { $Cdp = [string]$env:PRIZEPICKS_CDP }
$Cdp = $Cdp.Trim()
if ($Cdp) {
    Write-Host "  [WNBA step1] CDP attach: $Cdp" -ForegroundColor DarkGray
}
$StartTime = Get-Date
$script:ProgressDone = 0
$script:ProgressTotal = if ($SkipFetch) { 9 } else { 10 }  # 9 pipeline/copy stages + optional fetch

$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch { }

if (Test-Path "$Root\.venv\Scripts\Activate.ps1") {
    & "$Root\.venv\Scripts\Activate.ps1"
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  WNBA PIPELINE  |  $Date  |  $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Progress -Id 2 -Activity "WNBA Pipeline" -Status "Starting..." -PercentComplete 0

function Run-Step {
    param([string]$Label, [string]$Dir, [string]$Script, [string]$Arguments = "")
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        if ($Arguments) {
            $argArray = $Arguments -split ' '
            $output = & py -3.14 $Script @argArray 2>&1
        } else {
            $output = & py -3.14 $Script 2>&1
        }
        $exit = $LASTEXITCODE
        $output | ForEach-Object { Write-Host "      | $_" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      FAILED (exit $exit)" -ForegroundColor Red
            $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
            $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
            Write-Progress -Id 2 -Activity "WNBA Pipeline" -Status "$Label [FAILED] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
            return $false
        }
        Write-Host "      OK" -ForegroundColor Green
        $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
        $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
        Write-Progress -Id 2 -Activity "WNBA Pipeline" -Status "$Label [OK] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
        return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red
        $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
        $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
        Write-Progress -Id 2 -Activity "WNBA Pipeline" -Status "$Label [FAILED] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
        return $false
    } finally {
        Pop-Location
    }
}

# -- Cache management ---------------------------------------------------------
if ($RefreshCache) {
    Write-Host "  [Cache] Wiping WNBA ESPN cache..." -ForegroundColor Yellow
    Remove-Item "$WNBADir\wnba_espn_cache.csv" -Force -ErrorAction SilentlyContinue
    Write-Host "  [Cache] Done." -ForegroundColor Green
    Write-Host ""
}

# -- Dated output folder ------------------------------------------------------
$DateDir = "$OutRoot\$Date"
if (-not (Test-Path $DateDir)) {
    New-Item -ItemType Directory -Force -Path $DateDir | Out-Null
}

# =============================================================================
#  WNBA PIPELINE  (step1 → step9)
# =============================================================================
Write-Host "[ WNBA PIPELINE ]" -ForegroundColor Magenta
Write-Host ""

$ok = $true

# Step 1 — Fetch PrizePicks (league_id=3, WNBA)
if (-not $SkipFetch) {
    $step1Args = "--league_id 3 --playwright --timeout 90 --game_mode pickem --per_page 250 --max_pages 10 --sleep 1.2 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output step1_wnba_props.csv --date $Date"
    if ($Cdp) { $step1Args += " --cdp $Cdp" }
    if ($ok) { $ok = Run-Step "WNBA Step 1 - Fetch PrizePicks" $WNBADir ".\step1_fetch_prizepicks.py" $step1Args }
} else {
    Write-Host "  --> [SkipFetch] Using existing step1_wnba_props.csv" -ForegroundColor DarkGray
}

if ($ok) { $ok = Run-Step "WNBA Step 2 - Attach Pick Types" $WNBADir ".\step2_attach_picktypes.py" `
    "--input step1_wnba_props.csv --output step2_wnba_picktypes.csv" }

if ($ok) { $ok = Run-Step "WNBA Step 3 - Attach Defense" $WNBADir ".\step3_attach_defense.py" `
    "--input step2_wnba_picktypes.csv --defense wnba_defense_summary.csv --output step3_wnba_defense.csv" }

if ($ok) { $ok = Run-Step "WNBA Step 4 - Player Stats (ESPN)" $WNBADir ".\step4_fetch_player_stats.py" `
    "--slate step3_wnba_defense.csv --out step4_wnba_stats.csv --season 2026 --date $Date --days 35 --cache wnba_espn_cache.csv --sleep 0.8 --retries 4 --timeout 30 --debug-misses wnba_no_espn_debug.csv" }

if ($ok) { $ok = Run-Step "WNBA Step 5 - Line Hit Rates" $WNBADir ".\step5_add_line_hit_rates.py" `
    "--input step4_wnba_stats.csv --output step5_wnba_hitrates.csv" }

if ($ok) { $ok = Run-Step "WNBA Step 6 - Team Role Context" $WNBADir ".\step6_team_role_context.py" `
    "--input step5_wnba_hitrates.csv --output step6_wnba_context.csv" }

if ($ok) { $ok = Run-Step "WNBA Step 7 - Rank Props" $WNBADir ".\step7_rank_props.py" `
    "--input step6_wnba_context.csv --output step7_wnba_ranked.xlsx" }

# Step 7b — same unified edge overlay as NBA/NHL/Soccer (non-fatal on failure)
if ($ok) {
    $Step7bScript = Join-Path $Root "scripts\step7b_edge_score.py"
    if (Test-Path $Step7bScript) {
        Write-Host "  --> WNBA Step 7b - Unified edge score (ml_prob / edge_score)" -ForegroundColor Yellow
        Push-Location $Root
        try {
            & py -3.14 $Step7bScript --sport WNBA --step7-xlsx "$WNBADir\step7_wnba_ranked.xlsx" --repo-root $Root
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      step7b WARN (exit $LASTEXITCODE) — continuing" -ForegroundColor Yellow
            } else {
                Write-Host "      OK" -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  --> WNBA Step 7b SKIP (missing scripts\step7b_edge_score.py)" -ForegroundColor Yellow
    }
}

if ($ok) { $ok = Run-Step "WNBA Step 8 - Direction Context" $WNBADir ".\step8_add_direction_context.py" `
    "--input step7_wnba_ranked.xlsx --sheet ALL --output step8_wnba_direction.csv --xlsx step8_wnba_direction_clean.xlsx --date $Date" }

if ($ok) { $ok = Run-Step "WNBA Step 9 - Build Tickets" $WNBADir ".\step9_build_tickets.py" `
    "--input step8_wnba_direction_clean.xlsx --output wnba_best_tickets.xlsx --min_hit_rate 0.8 --legs 2,3,4" }

# =============================================================================
#  COPY OUTPUTS TO DATED FOLDER
# =============================================================================
if ($ok) {
    Write-Host ""
    Write-Host "[ COPYING OUTPUTS ]" -ForegroundColor Magenta

    $files = @("step1_wnba_props.csv","step7_wnba_ranked.xlsx","step8_wnba_direction.csv","step8_wnba_direction_clean.xlsx","wnba_best_tickets.xlsx")
    foreach ($f in $files) {
        $src = "$WNBADir\$f"
        if (Test-Path $src) {
            $dst = Join-Path $DateDir ("wnba_" + $Date + "_" + $f)
            Copy-Item $src $dst -Force
            Write-Host "  Copied: $f" -ForegroundColor Green
        }
    }
    # Canonical combined path: WNBA\data\outputs\step8_wnba_direction_clean.xlsx (matches Run-Combined)
    $dataOut = Join-Path $WNBADir "data\outputs"
    if (-not (Test-Path $dataOut)) {
        New-Item -ItemType Directory -Force -Path $dataOut | Out-Null
    }
    $step8Clean = "$WNBADir\step8_wnba_direction_clean.xlsx"
    if (Test-Path $step8Clean) {
        Copy-Item $step8Clean (Join-Path $dataOut "step8_wnba_direction_clean.xlsx") -Force
        Write-Host "  [WNBA] Canonical -> data\outputs\step8_wnba_direction_clean.xlsx" -ForegroundColor DarkGray
    }
    # Dated snapshot name for run_daily / audits (clean workbook)
    $step8Src = $step8Clean
    if (-not (Test-Path $step8Src)) { $step8Src = "$WNBADir\step8_wnba_direction.xlsx" }
    if (Test-Path $step8Src) {
        $step8Dst = Join-Path $DateDir ("step8_wnba_direction_clean_" + $Date + ".xlsx")
        Copy-Item $step8Src $step8Dst -Force
        Write-Host "  Copied: $(Split-Path -Leaf $step8Dst)" -ForegroundColor Green
    }
    $script:ProgressDone = [Math]::Min($script:ProgressDone + 1, $script:ProgressTotal)
    $pct = [int][Math]::Round(($script:ProgressDone / $script:ProgressTotal) * 100, 0)
    Write-Progress -Id 2 -Activity "WNBA Pipeline" -Status "Copy outputs [OK] ($script:ProgressDone/$script:ProgressTotal)" -PercentComplete $pct
}

# =============================================================================
#  SUMMARY
# =============================================================================
$Elapsed = (Get-Date) - $StartTime
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
if ($ok) {
    Write-Host "  WNBA DONE  |  $Date  |  Elapsed: $($Elapsed.ToString('mm\:ss'))" -ForegroundColor Cyan
    Write-Host "  Outputs → $DateDir" -ForegroundColor Green
} else {
    Write-Host "  WNBA FAILED  |  Check output above" -ForegroundColor Red
}
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Progress -Id 2 -Activity "WNBA Pipeline" -Completed

if ($ok) {
    exit 0
}
exit 1
