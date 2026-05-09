# ============================================================
#  WNBA PROP PIPELINE  -  Run Script
#
#  Usage:
#    .\run_wnba_pipeline.ps1              # Full WNBA pipeline run
#    .\run_wnba_pipeline.ps1 -Date 2026-07-15   # Specify date
#    .\run_wnba_pipeline.ps1 -RefreshCache      # Wipe ESPN cache + rebuild
#    .\run_wnba_pipeline.ps1 -SkipFetch         # Use existing step1 output
#    .\run_wnba_pipeline.ps1 -Cdp http://127.0.0.1:9222   # Fallback: WNBA step1 script (Playwright/CDP); NBA API has no CDP
#    .\run_wnba_pipeline.ps1 -UsePlaywright             # Fallback: Sports\WNBA\step1_fetch_prizepicks.py (browser)
#  Default step1 calls Sports\NBA\scripts\step1_fetch_prizepicks_api.py --league_id 3 (same fetch as NBA), output still outputs\<date>\wnba\step1_wnba_props.csv.
#  Env (optional): PROPORACLE_PP_CDP or PRIZEPICKS_CDP — same as -Cdp when -Cdp omitted.
#
#  Combined / game_date contract (2026-05): step1 anchors full-board game_date to --date;
#  step8 must not overwrite with start_time ET (see step8_add_direction_context.py). After step8,
#  Publish-WnbaStep8CleanArtifacts mirrors clean XLSX to outputs/<date>/ for dated consumers.
#  Canonical runtime outputs now live under outputs/<date>/wnba/.
# ============================================================
param(
    [string]$Date         = "",
    [switch]$RefreshCache,
    [switch]$SkipFetch,
    [string]$Cdp          = "",
    [switch]$UsePlaywright
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
$NbaApiStep1 = Join-Path $Root "Sports\NBA\scripts\step1_fetch_prizepicks_api.py"
$OutRoot = Join-Path $Root "outputs"

if (-not $Date) { $Date = Get-Date -Format "yyyy-MM-dd" }
$Cdp = $Cdp.Trim()
if (-not $Cdp) { $Cdp = [string]$env:PROPORACLE_PP_CDP }
if (-not $Cdp) { $Cdp = [string]$env:PRIZEPICKS_CDP }
$Cdp = $Cdp.Trim()
if ($Cdp) {
    Write-Host "  [WNBA step1] CDP attach: $Cdp" -ForegroundColor DarkGray
}
if ($UsePlaywright -and -not $Cdp) {
    Write-Host "  [WNBA step1] UsePlaywright: in-browser PrizePicks fetch (HTTP API disabled)" -ForegroundColor DarkGray
}
$StartTime = Get-Date
$script:ProgressDone = 0
$script:ProgressTotal = if ($SkipFetch) { 8 } else { 9 }  # pipeline stages + optional fetch (no dated copy batch)

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

function Split-QuotedArgs([string]$s) {
    if (-not $s) { return @() }
    $parts = [System.Collections.ArrayList]@()
    $i = 0
    while ($i -lt $s.Length) {
        while ($i -lt $s.Length -and [char]::IsWhiteSpace($s[$i])) { $i++ }
        if ($i -ge $s.Length) { break }
        if ($s[$i] -eq [char]34) {
            $i++
            $start = $i
            while ($i -lt $s.Length -and $s[$i] -ne [char]34) { $i++ }
            [void]$parts.Add($s.Substring($start, [Math]::Max(0, $i - $start)))
            if ($i -lt $s.Length) { $i++ }
        } else {
            $start = $i
            while ($i -lt $s.Length -and -not [char]::IsWhiteSpace($s[$i])) { $i++ }
            [void]$parts.Add($s.Substring($start, $i - $start))
        }
    }
    return ,$parts.ToArray()
}

function Run-Step {
    param([string]$Label, [string]$Dir, [string]$Script, [string]$Arguments = "")
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        if ($Arguments) {
            $argArray = Split-QuotedArgs $Arguments
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

function Publish-WnbaStep8CleanArtifacts {
    $step8Clean = Join-Path $WnbaRunOutDir "step8_wnba_direction_clean.xlsx"
    if (-not (Test-Path -LiteralPath $step8Clean)) {
        Write-Host "  [WNBA publish] skip — no step8_wnba_direction_clean.xlsx" -ForegroundColor DarkGray
        return
    }
    $step8Dst = Join-Path $DateDir ("step8_wnba_direction_clean_" + $Date + ".xlsx")
    Copy-Item $step8Clean $step8Dst -Force
    Write-Host "  [WNBA publish] Combined dated input -> $(Split-Path -Leaf $step8Dst)" -ForegroundColor DarkGray
}

# -- Cache management ---------------------------------------------------------
if ($RefreshCache) {
    Write-Host "  [Cache] Wiping WNBA ESPN cache..." -ForegroundColor Yellow
    Remove-Item "$WNBADir\wnba_espn_cache.csv" -Force -ErrorAction SilentlyContinue
    Write-Host "  [Cache] Done." -ForegroundColor Green
    Write-Host ""
}

# -- Dated output folder (step8 clean mirror only; written by Publish-WnbaStep8CleanArtifacts) ---
$DateDir = "$OutRoot\$Date"
if (-not (Test-Path $DateDir)) {
    New-Item -ItemType Directory -Force -Path $DateDir | Out-Null
}
# Canonical WNBA runtime output folder.
$WnbaRunOutDir = Join-Path $DateDir "wnba"
if (-not (Test-Path $WnbaRunOutDir)) {
    New-Item -ItemType Directory -Force -Path $WnbaRunOutDir | Out-Null
}

# =============================================================================
#  WNBA PIPELINE  (step1 → step9)
# =============================================================================
Write-Host "[ WNBA PIPELINE ]" -ForegroundColor Magenta
Write-Host ""

$ok = $true

# Step 1 — PrizePicks: default = NBA API script (league_id 3), same as NBA step1; output paths stay under outputs\<date>\wnba\.
# Browser/CDP: Sports\WNBA\step1_fetch_prizepicks.py only (NBA API has no Playwright).
if (-not $SkipFetch) {
    if ($UsePlaywright -or $Cdp) {
        $step1Args = "--league_id 3 --game_mode pickem --per_page 250 --max_pages 10 --sleep 1.2 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output `"$WnbaRunOutDir\step1_wnba_props.csv`" --date $Date"
        if ($UsePlaywright) {
            $step1Args = "--league_id 3 --playwright --timeout 90 --game_mode pickem --per_page 250 --max_pages 10 --sleep 1.2 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output `"$WnbaRunOutDir\step1_wnba_props.csv`" --date $Date"
        }
        if ($Cdp) { $step1Args += " --cdp $Cdp" }
        if ($ok) { $ok = Run-Step "WNBA Step 1 - Fetch PrizePicks (browser)" $WNBADir ".\step1_fetch_prizepicks.py" $step1Args }
    } else {
        if (-not (Test-Path -LiteralPath $NbaApiStep1)) {
            Write-Host "  ERROR: NBA API fetcher not found: $NbaApiStep1" -ForegroundColor Red
            $ok = $false
        } else {
            $step1Args = "--league_id 3 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output `"$WnbaRunOutDir\step1_wnba_props.csv`" --date $Date"
            if ($ok) { $ok = Run-Step "WNBA Step 1 - Fetch PrizePicks (NBA API, league 3)" $WNBADir $NbaApiStep1 $step1Args }
        }
    }
} else {
    Write-Host "  --> [SkipFetch] Using existing $WnbaRunOutDir\step1_wnba_props.csv" -ForegroundColor DarkGray
    Write-Host "  [WNBA] If combined dropped WNBA rows, re-run step1 once (no SkipFetch) after board/game_date policy changes." -ForegroundColor DarkYellow
}

if ($ok) { $ok = Run-Step "WNBA Step 2 - Attach Pick Types" $WNBADir ".\step2_attach_picktypes.py" `
    "--input `"$WnbaRunOutDir\step1_wnba_props.csv`" --output `"$WnbaRunOutDir\step2_wnba_picktypes.csv`"" }

if ($ok) { $ok = Run-Step "WNBA Step 3 - Attach Defense" $WNBADir ".\step3_attach_defense.py" `
    "--input `"$WnbaRunOutDir\step2_wnba_picktypes.csv`" --defense wnba_defense_summary.csv --output `"$WnbaRunOutDir\step3_wnba_defense.csv`"" }

if ($ok) { $ok = Run-Step "WNBA Step 4 - Player Stats (ESPN)" $WNBADir ".\step4_fetch_player_stats.py" `
    "--slate `"$WnbaRunOutDir\step3_wnba_defense.csv`" --out `"$WnbaRunOutDir\step4_wnba_stats.csv`" --season 2026 --date $Date --days 35 --cache wnba_espn_cache.csv --sleep 0.8 --retries 4 --timeout 30 --debug-misses wnba_no_espn_debug.csv" }

if ($ok) { $ok = Run-Step "WNBA Step 5 - Line Hit Rates" $WNBADir ".\step5_add_line_hit_rates.py" `
    "--input `"$WnbaRunOutDir\step4_wnba_stats.csv`" --output `"$WnbaRunOutDir\step5_wnba_hitrates.csv`"" }

if ($ok) { $ok = Run-Step "WNBA Step 6 - Team Role Context" $WNBADir ".\step6_team_role_context.py" `
    "--input `"$WnbaRunOutDir\step5_wnba_hitrates.csv`" --output `"$WnbaRunOutDir\step6_wnba_context.csv`"" }

if ($ok) { $ok = Run-Step "WNBA Step 7 - Rank Props" $WNBADir ".\step7_rank_props.py" `
    "--input `"$WnbaRunOutDir\step6_wnba_context.csv`" --output `"$WnbaRunOutDir\step7_wnba_ranked.xlsx`"" }

# Step 7b — same unified edge overlay as NBA/NHL/Soccer (non-fatal on failure)
if ($ok) {
    $Step7bScript = Join-Path $Root "scripts\step7b_edge_score.py"
    if (Test-Path $Step7bScript) {
        Write-Host "  --> WNBA Step 7b - Unified edge score (ml_prob / edge_score)" -ForegroundColor Yellow
        Push-Location $Root
        try {
            & py -3.14 $Step7bScript --sport WNBA --step7-xlsx "$WnbaRunOutDir\step7_wnba_ranked.xlsx" --repo-root $Root
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
    "--input `"$WnbaRunOutDir\step7_wnba_ranked.xlsx`" --sheet ALL --output `"$WnbaRunOutDir\step8_wnba_direction.csv`" --xlsx `"$WnbaRunOutDir\step8_wnba_direction_clean.xlsx`" --date $Date" }
if ($ok) { Publish-WnbaStep8CleanArtifacts }

if ($ok) { $ok = Run-Step "WNBA Step 9 - Build Tickets" $WNBADir ".\step9_build_tickets.py" `
    "--input `"$WnbaRunOutDir\step8_wnba_direction_clean.xlsx`" --output `"$WnbaRunOutDir\wnba_best_tickets.xlsx`" --min_hit_rate 0.8 --legs 2,3,4" }

# Web: merge WNBA rows into slate_latest.json + slate_sport_wnba.json (templates + mobile/www)
if ($ok) {
    Write-Host "  --> WNBA — Publish slate to UI JSON" -ForegroundColor Yellow
    Push-Location $Root
    try {
        & py -3.14 (Join-Path $Root "scripts\publish_wnba_slate_to_ui.py") --date $Date
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      WARN: publish_wnba_slate_to_ui exit $LASTEXITCODE (Slate Explorer may lack WNBA until combined run)" -ForegroundColor Yellow
        } else {
            Write-Host "      OK" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
}

# =============================================================================
#  SUMMARY
# =============================================================================
$Elapsed = (Get-Date) - $StartTime
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
if ($ok) {
    Write-Host "  WNBA DONE  |  $Date  |  Elapsed: $($Elapsed.ToString('mm\:ss'))" -ForegroundColor Cyan
    Write-Host "  Canonical runtime → $WnbaRunOutDir  |  Dated step8 mirror → $DateDir" -ForegroundColor Green
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
