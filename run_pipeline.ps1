#requires -Version 7.2
# ============================================================
#  PROP PIPELINE  -  Master Run Script  [MULTI-SPORT]
#
#  Usage:
#    .\regenerate_defense_caches.ps1           # Refresh NBA/WNBA/NHL/Soccer defense CSVs (tier labels) — run before pipeline if defense logic changed
#    .\run_pipeline.ps1                        # All sports parallel + Combined
#    .\run_pipeline.ps1 -NBAOnly               # NBA only + Combined
#    .\run_pipeline.ps1 -CBBOnly               # CBB only + Combined
#    .\run_pipeline.ps1 -CFBOnly               # College Football only + Combined
#    .\run_pipeline.ps1 -NHLOnly               # NHL only + Combined
#    .\run_pipeline.ps1 -MLBOnly               # MLB only + Combined
#    .\run_pipeline.ps1 -SoccerOnly            # Soccer only + Combined
#    .\run_pipeline.ps1 -TennisOnly           # Tennis (light pipeline) + Combined
#    .\run_pipeline.ps1 -WNBAOnly              # WNBA only (delegates to scripts\run_wnba_pipeline.ps1)
#    .\run_pipeline.ps1 -WNBAOnly -WNBACdp http://127.0.0.1:9222   # PrizePicks via Chrome CDP (DataDome)
#  WNBA / combined contract: step1 HTTP API by default (curl_cffi); optional CDP/-UsePlaywright; step8 preserves game_date;
#  scripts\run_wnba_pipeline.ps1 publishes step8 clean to outputs/<date>/ after step8 (before step9).
#  WNBA CDP env (optional): PROPORACLE_PP_CDP or PRIZEPICKS_CDP — used when -WNBACdp omitted.
#    .\run_pipeline.ps1 -NFLOnly               # NFL only (delegates to scripts\run_nfl_pipeline.ps1) + Combined
#    .\run_pipeline.ps1 -ForceWNBA           # Include WNBA in full parallel run before season start (QA)
#    .\run_pipeline.ps1 -CombinedOnly          # Re-run combined + web tickets (multi-sport /tickets JSON)
#    .\run_pipeline.ps1 -CombinedOnly -WebEvOnly   # Stricter /tickets: positive-EV gate only (+ Tennis bypass)
#    .\run_pipeline.ps1 -SkipFetch             # Skip step1 fetch for whatever sport(s) run
#    .\run_pipeline.ps1 -NBAOnly -SkipFetch    # NBA steps 2-8 + Combined
#    .\run_pipeline.ps1 -NHLOnly -SkipFetch    # NHL steps 2-8 + Combined
#    .\run_pipeline.ps1 -SoccerOnly -SkipFetch # Soccer steps 2-8 + Combined
#    .\run_pipeline.ps1 -TennisOnly -SkipFetch # Tennis steps 2-8 + Combined (no step1 fetch)
#    .\run_pipeline.ps1 -TennisOnly -TennisDate 2026-05-14   # Override slate date (default: day after -Date)
#    .\run_pipeline.ps1 -RefreshCache          # Wipe + rebuild ESPN cache before NBA
#    .\run_pipeline.ps1 -CacheAgeDays 7        # Auto-wipe cache if older than N days
#    .\run_pipeline.ps1 -SkipDailyGrader       # Skip run_grader + grade HTML git push after combined
#    .\run_pipeline.ps1 -UseAltBooks           # Optional: include Underdog + DraftKings cross-book inputs
#    .\run_pipeline.ps1 -SkipAltBooks          # Legacy flag (no-op unless -UseAltBooks is set)
#
#  Combined always auto-includes every sport whose step8 output exists on disk.
#  No -Include flags needed -- just run any sport, combined picks it up.
#
#  Prefer .\run_pipeline.ps1 from repo root; this copy under scripts\ is equivalent.
#  After combined + git push, runs scripts\run_grader.ps1 for yesterday's slate, builds
#  ticket_eval HTML for the pipeline date (today) so Grades matches live /tickets, then pushes grade artifacts.
#
# ENTRY POINTS
#   Full daily run  : scripts\run_daily.ps1 [-Date YYYY-MM-DD]
#     STEP C calls  : run_pipeline.ps1 -Date $Today -ForceAll -SkipCombined -SkipPush
#     STEP D calls  : run_pipeline.ps1 -Date $Today -CombinedOnly -DQWarnOnly
#   Manual rebuild  : .\run_pipeline.ps1 -Date YYYY-MM-DD [-CombinedOnly]
#
# ============================================================
param(
    [string]$Date       = "",
    # Prefer env ODDS_API_KEY; pass -OddsApiKey only for one-off overrides (never commit keys).
    [string]$OddsApiKey = "",
    [switch]$NBAOnly,
    [switch]$CBBOnly,
    [switch]$CFBOnly,
    [switch]$NHLOnly,
    [switch]$MLBOnly,
    # Fast MLB verification mode: run MLB through Step 4 and exit.
    [switch]$MLBVerify,
    [switch]$SoccerOnly,
    [switch]$TennisOnly,
    [string]$TennisDate = "",
    [switch]$WNBAOnly,
    [switch]$NFLOnly,
    # Run WNBA in the full parallel block even if pipeline date is before WNBA season start (default off).
    [switch]$ForceWNBA,
    [switch]$CombinedOnly,
    [switch]$SkipFetch,
    [switch]$RefreshCache,
    [switch]$ForceAll,
    [switch]$SkipDailyGrader,
    [switch]$RunPayoutEngine,
    # Skip Soccer defense refresh network fetch (use cached cache\soccer_defense_summary.csv).
    [switch]$SkipDefenseRefresh,
    # Used by scripts/run_daily.ps1 to execute sport pipelines in STEP C
    # and defer combined generation to STEP D.
    [switch]$SkipCombined,
    # Used by scripts/run_daily.ps1 so git push is only handled once there.
    [switch]$SkipPush,
    [switch]$UseAltBooks,
    [switch]$SkipAltBooks,
    [int]$CacheAgeDays = 7,
    # By default /tickets JSON includes MLB/NHL/Soccer slips (not only strict positive-EV + Tennis).
    # Pass -WebEvOnly to restore the stricter web JSON filter.
    [switch]$WebEvOnly,
    # Compatibility flag passed by run_daily combined-only invocation.
    # Intentionally no-op here; ticket quality warnings are handled inside downstream scripts.
    [switch]$DQWarnOnly,
    # Chrome CDP URL for WNBA step1 (PrizePicks); parallel WNBA job receives this explicitly (env may not propagate).
    [string]$WNBACdp = ""
)

$ErrorActionPreference = "Continue"
$script:CombinedRanThisSession = $false

if (-not $OddsApiKey) {
    $OddsApiKey = [string]$env:ODDS_API_KEY
}

$WNBACdp = $WNBACdp.Trim()
if (-not $WNBACdp) { $WNBACdp = [string]$env:PROPORACLE_PP_CDP }
if (-not $WNBACdp) { $WNBACdp = [string]$env:PRIZEPICKS_CDP }
$WNBACdp = $WNBACdp.Trim()
if ($WNBACdp) {
    Write-Host "  [WNBA] CDP for step1 fetch: $WNBACdp" -ForegroundColor DarkGray
}

# -- Date ---------------------------------------------------------------------
if (-not $Date) {
    $Date = Get-Date -Format "yyyy-MM-dd"
    Write-Host "  [Date] No date specified, using today: $Date" -ForegroundColor DarkGray
} else {
    if ($Date -match "^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{4}$|^\d{1,2}-\d{1,2}-\d{4}$") {
        Write-Host "  [Date] Using specified date: $Date" -ForegroundColor Cyan
    } else {
        Write-Host "  [Date] ERROR: Invalid date format '$Date'. Use: 2026-03-12" -ForegroundColor Red
        exit 1
    }
}

# Tennis targets the next calendar day vs pipeline -Date (early ET / next-day slate). Override with -TennisDate.
# Step 8 / dated mirrors use $TennisDate; output folder stays outputs\$Date.
if (-not $TennisDate) {
    try {
        $TennisDate = ([datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)).AddDays(1).ToString("yyyy-MM-dd")
    } catch {
        $TennisDate = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
    }
    Write-Host "  [Tennis] No -TennisDate: using day after pipeline -Date ($Date -> $TennisDate)" -ForegroundColor DarkGray
} else {
    Write-Host "  [Tennis] Using specified TennisDate: $TennisDate" -ForegroundColor Cyan
}

if ($MLBVerify -and -not $MLBOnly) {
    Write-Host "  [MLBVerify] Enabling -MLBOnly automatically." -ForegroundColor DarkGray
    $MLBOnly = $true
}

# MLB step4 ESPN/cache season: use slate calendar year (2026 slates must not pass --season 2025).
try {
    $MLBSeasonYear = ([datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)).Year
    $CFBSeasonYear = $MLBSeasonYear
} catch {
    $MLBSeasonYear = (Get-Date).Year
    $CFBSeasonYear = $MLBSeasonYear
}

$StartTime = Get-Date

# -- Paths --------------------------------------------------------------------
# Script may live at repo root or under scripts\; jobs also need a stable $Root for absolute step8 paths.
$Root = $PSScriptRoot
if ((Split-Path -Leaf $Root) -eq "scripts") {
    $Root = Split-Path -Parent $Root
}
# All sport trees live under <repo>\Sports\ (not repo root).
$SportsRoot = Join-Path $Root "Sports"
$NBADir    = Join-Path $SportsRoot "NBA"
$CBBDir    = Join-Path $SportsRoot "CBB"
$CFBDir    = Join-Path $SportsRoot "CFB"
$NHLDir    = Join-Path $SportsRoot "NHL"
$MLBDir    = Join-Path $SportsRoot "MLB"
$SoccerDir = Join-Path $SportsRoot "Soccer"
$TennisDir = Join-Path $SportsRoot "Tennis"
$WNBADir   = Join-Path $SportsRoot "WNBA"
$NFLDir    = Join-Path $SportsRoot "NFL"
# WNBA regular season: include in full parallel runs on/after this date (ISO yyyy-MM-dd).
# 2026 opener starts May 1, so keep WNBA active for same-day fresh slates.
$WNBA_SEASON_START = "2026-05-01"
$OutDir    = Join-Path $Root "outputs\$Date"
$NBARunOutDir = Join-Path $OutDir "nba"
$NBA1HRunOutDir = Join-Path $OutDir "nba1h"
$NBA1QRunOutDir = Join-Path $OutDir "nba1q"
$NHLRunOutDir = Join-Path $OutDir "nhl"
$SoccerRunOutDir = Join-Path $OutDir "soccer"
$TennisRunOutDir = Join-Path $OutDir "tennis"
$MLBRunOutDir = Join-Path $OutDir "mlb"
$NFLRunOutDir = Join-Path $OutDir "nfl"
$CBBRunOutDir = Join-Path $OutDir "cbb"
$CFBRunOutDir = Join-Path $OutDir "cfb"
$CanonicalOutDir = Join-Path $OutDir "canonical"
$CanonicalPlatformUiDir = Join-Path $CanonicalOutDir "platform_ui"
$CanonicalMobileAppDir = Join-Path $CanonicalOutDir "mobile_app"
$WebOutDir = Join-Path $Root "ui_runner\templates"
$UiDataDir = Join-Path $Root "ui_runner\data"
$UiDataBackupsDir = Join-Path $UiDataDir "backups"
$MobileWwwDir = Join-Path $Root "mobile\www"

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
if (-not (Test-Path $NBARunOutDir)) { New-Item -ItemType Directory -Force -Path $NBARunOutDir | Out-Null }
if (-not (Test-Path $NBA1HRunOutDir)) { New-Item -ItemType Directory -Force -Path $NBA1HRunOutDir | Out-Null }
if (-not (Test-Path $NBA1QRunOutDir)) { New-Item -ItemType Directory -Force -Path $NBA1QRunOutDir | Out-Null }
if (-not (Test-Path $NHLRunOutDir)) { New-Item -ItemType Directory -Force -Path $NHLRunOutDir | Out-Null }
if (-not (Test-Path $SoccerRunOutDir)) { New-Item -ItemType Directory -Force -Path $SoccerRunOutDir | Out-Null }
if (-not (Test-Path $TennisRunOutDir)) { New-Item -ItemType Directory -Force -Path $TennisRunOutDir | Out-Null }
if (-not (Test-Path $MLBRunOutDir)) { New-Item -ItemType Directory -Force -Path $MLBRunOutDir | Out-Null }
if (-not (Test-Path $NFLRunOutDir)) { New-Item -ItemType Directory -Force -Path $NFLRunOutDir | Out-Null }
if (-not (Test-Path $CBBRunOutDir)) { New-Item -ItemType Directory -Force -Path $CBBRunOutDir | Out-Null }
if (-not (Test-Path $CFBRunOutDir)) { New-Item -ItemType Directory -Force -Path $CFBRunOutDir | Out-Null }
if (-not (Test-Path $CanonicalOutDir)) { New-Item -ItemType Directory -Force -Path $CanonicalOutDir | Out-Null }
if (-not (Test-Path $CanonicalPlatformUiDir)) { New-Item -ItemType Directory -Force -Path $CanonicalPlatformUiDir | Out-Null }
if (-not (Test-Path $UiDataDir)) { New-Item -ItemType Directory -Force -Path $UiDataDir | Out-Null }
if (-not (Test-Path $UiDataBackupsDir)) { New-Item -ItemType Directory -Force -Path $UiDataBackupsDir | Out-Null }
if (-not (Test-Path $CanonicalMobileAppDir)) { New-Item -ItemType Directory -Force -Path $CanonicalMobileAppDir | Out-Null }

# -- Encoding -----------------------------------------------------------------
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch { }

# -- Activate venv ------------------------------------------------------------
if (Test-Path (Join-Path $Root ".venv\Scripts\Activate.ps1")) {
    & (Join-Path $Root ".venv\Scripts\Activate.ps1")
}

$__graderPs1 = Join-Path $Root "scripts\run_post_pipeline_grader.ps1"
if (Test-Path $__graderPs1) {
    . $__graderPs1
} else {
    function Run-PostPipelineGrader {
        Write-Host "[PostGrader] run_post_pipeline_grader.ps1 missing — skip" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  PROP PIPELINE  -- $Date -- $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# -- Helper: auto-wipe ESPN cache if stale ------------------------------------
function Check-AutoRefreshCache {
    $cacheFile = Join-Path $NBADir "nba_espn_boxscore_cache.csv"
    if (Test-Path $cacheFile) {
        $age = (Get-Date) - (Get-Item $cacheFile).LastWriteTime
        if ($age.TotalDays -gt $CacheAgeDays) {
            Write-Host "  [Cache] ESPN cache is $([math]::Round($age.TotalDays,1)) days old (threshold: $CacheAgeDays). Auto-wiping..." -ForegroundColor Yellow
            Remove-Item (Join-Path $NBADir "nba_espn_boxscore_cache.csv") -Force -ErrorAction SilentlyContinue
            Remove-Item (Join-Path $NBADir "nba_to_espn_id_map.csv")      -Force -ErrorAction SilentlyContinue
            Write-Host "  [Cache] Wiped. Will rebuild fresh." -ForegroundColor Green
        } else {
            Write-Host "  [Cache] ESPN cache is $([math]::Round($age.TotalDays,1)) days old -- keeping." -ForegroundColor DarkGray
        }
    }
}

# -- Helper: run one step synchronously ---------------------------------------
function Run-Step {
    param(
        [string]$Label,
        [string]$Dir,
        [string]$Script,
        [string]$Arguments = "",
        [int]$TimeoutSeconds = 0
    )
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        # Child Python inherits these; avoids UnicodeEncodeError on emoji logs (e.g. MLB step1) if the shell was cold-started without UTF-8.
        $env:PYTHONUTF8       = "1"
        $env:PYTHONIOENCODING = "utf-8"
        $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
        Write-Host "        CMD: $cmd" -ForegroundColor DarkGray
        if ($TimeoutSeconds -gt 0) {
            Write-Host "        Timeout: ${TimeoutSeconds}s" -ForegroundColor DarkGray
            $job = Start-Job -ScriptBlock {
                param($Command, $WorkingDir)
                Set-Location $WorkingDir
                $env:PYTHONUTF8       = "1"
                $env:PYTHONIOENCODING = "utf-8"
                $output = Invoke-Expression $Command 2>&1
                $exit   = $LASTEXITCODE
                [pscustomobject]@{
                    Output = $output
                    Exit   = $exit
                }
            } -ArgumentList $cmd, (Get-Location).Path
            $completed = Wait-Job -Job $job -Timeout $TimeoutSeconds
            if (-not $completed) {
                Stop-Job -Job $job -ErrorAction SilentlyContinue
                Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                Write-Host "      FAILED (timeout after ${TimeoutSeconds}s)" -ForegroundColor Red
                return $false
            }
            $result = Receive-Job -Job $job
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
            $output = @($result.Output)
            $exit   = [int]$result.Exit
        } else {
            $output = Invoke-Expression $cmd 2>&1
            $exit   = $LASTEXITCODE
        }
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) { Write-Host "      FAILED (exit $exit)" -ForegroundColor Red; return $false }
        Write-Host "      OK" -ForegroundColor Green; return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red; return $false
    } finally {
        Pop-Location
    }
}

# Rebuild mobile/www from ui_runner/templates (slate + tickets JSON, pipeline_status, etc.).
# Called from Run-Combined after every successful combined_slate_tickets.py — all entry points
# (-CombinedOnly, *Only + combined, full parallel) funnel through Run-Combined.
function Invoke-PropOracleMobileBundle {
    return (Run-Step "Generate mobile bundle" $Root ".\scripts\generate_mobile_bundle.py" "")
}

function Invoke-NBAStep1Fetch {
    param(
        [string]$WorkDir,
        [string]$PipelineDate,
        [string]$OutputPath
    )
    Write-Host "  --> NBA Step 1 - Fetch PrizePicks (emergency / recovery)" -ForegroundColor Yellow
    Push-Location $WorkDir
    try {
        $env:PYTHONUTF8 = "1"
        $env:PYTHONIOENCODING = "utf-8"
        $outDir = Split-Path -Parent $OutputPath
        if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
            New-Item -ItemType Directory -Force -Path $outDir | Out-Null
        }
        $output = & py -3.14 ".\scripts\step1_fetch_prizepicks_api.py" `
            --league_id 7 --game_mode pickem --per_page 250 --max_pages 5 `
            --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 `
            --replace --date $PipelineDate --allow-nearest-future `
            --output $OutputPath 2>&1
        $exit = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) { Write-Host "      FAILED (exit $exit)" -ForegroundColor Red; return $false }
        Write-Host "      OK" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red
        return $false
    } finally {
        Pop-Location
    }
}

function Invoke-MLBStep1Fetch {
    param(
        [string]$WorkDir,
        [string]$PipelineDate,
        [string]$OutputPath = "step1_mlb_props.csv"
    )
    Write-Host "  --> MLB Step 1 - Fetch PrizePicks (direct API, then Playwright if needed)" -ForegroundColor Yellow
    Push-Location $WorkDir
    try {
        $env:PYTHONUTF8       = "1"
        $env:PYTHONIOENCODING = "utf-8"
        # Use call operator (&) so $LASTEXITCODE reflects Python — Invoke-Expression + capture can leave a stale 0 and skip Playwright after a failed fetch.
        $cmd1Display = "py -3.14 -u .\scripts\step1_fetch_prizepicks_mlb.py --date $PipelineDate --output $OutputPath --api-retries 2 ..."
        Write-Host "        CMD: $cmd1Display" -ForegroundColor DarkGray
        $output = & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
            --date $PipelineDate --output $OutputPath `
            --api-retries 2 --api-session-waves 1 `
            --api-wave-gap-min 8 --api-wave-gap-max 15 `
            --api-403-cooldown-after 2 --api-403-cooldown-seconds 20 `
            --api-403-cooldown-jitter-min 4 --api-403-cooldown-jitter-max 10 2>&1
        $exit = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      MLB direct API failed (exit $exit); trying Playwright..." -ForegroundColor Yellow
            $cmd2Display = "py -3.14 -u .\scripts\step1_fetch_prizepicks_mlb.py --playwright --date $PipelineDate --output $OutputPath"
            Write-Host "        CMD: $cmd2Display" -ForegroundColor DarkGray
            $output = & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
                --playwright --timeout 120 --retries 1 --retry_delay 5 `
                --date $PipelineDate --output $OutputPath 2>&1
            $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        }
        if ($exit -ne 0) { Write-Host "      FAILED (exit $exit)" -ForegroundColor Red; return $false }
        Write-Host "      OK" -ForegroundColor Green; return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red; return $false
    } finally {
        Pop-Location
    }
}

function Get-Step1DateHealth {
    param(
        [string]$CsvPath,
        [string]$TargetDate
    )
    if (-not (Test-Path $CsvPath)) { return @{ ok = $false; rows = 0; reason = "missing_file" } }
    try {
        $rows = Import-Csv -Path $CsvPath
    } catch {
        return @{ ok = $false; rows = 0; reason = "read_error" }
    }
    if (-not $rows -or $rows.Count -eq 0) { return @{ ok = $false; rows = 0; reason = "empty_file" } }

    $match = @()
    if ($rows[0].PSObject.Properties.Name -contains "game_date") {
        $match = $rows | Where-Object { (($_.game_date | ForEach-Object { "$_".Trim() })) -eq $TargetDate }
    } elseif ($rows[0].PSObject.Properties.Name -contains "start_time") {
        $match = $rows | Where-Object { "$($_.start_time)".Length -ge 10 -and "$($_.start_time)".Substring(0, 10) -eq $TargetDate }
    } else {
        return @{ ok = $false; rows = $rows.Count; reason = "missing_date_columns" }
    }
    $reason = if ($match.Count -gt 0) { "ok" } else { "date_mismatch" }
    return @{ ok = ($match.Count -gt 0); rows = $rows.Count; reason = $reason }
}

function Get-NBAStep1DateHealth {
    param([string]$CsvPath, [string]$TargetDate)
    return (Get-Step1DateHealth -CsvPath $CsvPath -TargetDate $TargetDate)
}

function Get-MLBStep1DateHealth {
    param([string]$CsvPath, [string]$TargetDate)
    return (Get-Step1DateHealth -CsvPath $CsvPath -TargetDate $TargetDate)
}

function Clear-NBAGeneratedOutputs {
    param([string]$BaseDir)
    foreach ($p in @(
        "step1_pp_props_today.csv",
        "step2_with_picktypes.csv",
        "step3_with_defense.csv",
        "step4_with_stats.csv",
        "step5_with_hit_rates.csv",
        "step6_with_team_role_context.csv",
        "step6a_with_opp_stats.csv",
        "step6b_with_game_context.csv",
        "step6c_with_schedule_flags.csv",
        "step6d_with_h2h.csv",
        "step7_ranked_props.xlsx",
        "step8_all_direction.csv",
        "step8_all_direction_clean.xlsx"
    )) {
        Remove-Item (Join-Path $BaseDir $p) -Force -ErrorAction SilentlyContinue
    }
}

function Clear-MLBGeneratedOutputs {
    param([string]$BaseDir)
    foreach ($p in @(
        "step1_mlb_props.csv",
        "step2_mlb_picktypes.csv",
        "step3_mlb_with_defense.csv",
        "step4_mlb_with_stats.csv",
        "step5_mlb_hit_rates.csv",
        "step6_mlb_role_context.csv",
        "step7_mlb_ranked.xlsx",
        "step8_mlb_direction.csv",
        "step8_mlb_direction_clean.xlsx"
    )) {
        Remove-Item (Join-Path $BaseDir $p) -Force -ErrorAction SilentlyContinue
    }
}

# -- Helper: NBA period sub-slate pipelines (NBA1H / NBA1Q) -------------------
function Run-NBAPeriodPipeline {
    param(
        [string]$Tag,            # nba1h or nba1q
        [string]$LeagueId,       # PrizePicks league id
        [switch]$SkipFetchStep
    )
    $tagLower = ($Tag ?? "").ToLowerInvariant()
    if ($tagLower -notin @("nba1h", "nba1q")) {
        Write-Host "  [NBA-PERIOD] Unknown tag '$Tag' (expected nba1h|nba1q)" -ForegroundColor Yellow
        return $false
    }

    Write-Host ""
    Write-Host ('[ NBA PERIOD PIPELINE: ' + $tagLower + ' ]') -ForegroundColor Magenta

    $periodOutDir = Join-Path $OutDir $tagLower
    if (-not (Test-Path $periodOutDir)) { New-Item -ItemType Directory -Force -Path $periodOutDir | Out-Null }
    $step1 = Join-Path $periodOutDir "step1_${tagLower}_props.csv"
    $step2 = Join-Path $periodOutDir "step2_${tagLower}_picktypes.csv"
    $step3 = Join-Path $periodOutDir "step3_${tagLower}_with_defense.csv"
    $step4 = Join-Path $periodOutDir "step4_${tagLower}_with_stats.csv"
    $step5 = Join-Path $periodOutDir "step5_${tagLower}_with_hit_rates.csv"
    $step6 = Join-Path $periodOutDir "step6_${tagLower}_with_team_role_context.csv"
    $step7 = Join-Path $periodOutDir "step7_${tagLower}_ranked_props.xlsx"
    $step8Csv = Join-Path $periodOutDir "step8_${tagLower}_direction.csv"
    $step8Xlsx = Join-Path $periodOutDir "step8_${tagLower}_direction_clean.xlsx"
    $datedOut = Join-Path $OutDir "step8_${tagLower}_direction_clean_${Date}.xlsx"

    $ok = $true
    if (-not $SkipFetchStep) {
        Write-Host "  --> ${tagLower} Step 1 - Fetch PrizePicks" -ForegroundColor Yellow
        Push-Location $NBADir
        try {
            $cmd = "py -3.14 `".\scripts\step1_fetch_prizepicks_api.py`" --league_id $LeagueId --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output `"$step1`" --date $Date"
            Write-Host "        CMD: $cmd" -ForegroundColor DarkGray
            $out = Invoke-Expression $cmd 2>&1
            $exit = $LASTEXITCODE
            foreach ($line in $out) { Write-Host "        $line" -ForegroundColor DarkGray }
            if ($exit -ne 0) {
                $joined = ($out | Out-String)
                if ($joined -match "No projections returned") {
                    Write-Host "      No live $tagLower board right now — clearing stale period files and skipping." -ForegroundColor DarkGray
                    foreach ($stale in @($step2, $step3, $step4, $step5, $step6, $step7, $step8Csv, $step8Xlsx)) {
                        Remove-Item $stale -Force -ErrorAction SilentlyContinue
                    }
                    Remove-Item $datedOut -Force -ErrorAction SilentlyContinue
                    return $true
                }
                Write-Host "      FAILED (exit $exit)" -ForegroundColor Red
                return $false
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  [$tagLower] Skipping step1 fetch -- using existing $step1" -ForegroundColor DarkGray
    }

    if ($ok) { $ok = Run-Step "${tagLower} Step 2 - Attach Pick Types"      $NBADir ".\scripts\step2_attach_picktypes.py"               "--input $step1 --output $step2" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 3 - Attach Defense"         $NBADir ".\scripts\step3_attach_defense.py"                 "--input $step2 --defense data\cache\defense_team_summary.csv --output $step3" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 4 - Player Stats (ESPN)"    $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate $step3 --out $step4 --date $Date" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 5 - Line Hit Rates"         $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input $step4 --output $step5 --compute10" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 6 - Team Role Context"      $NBADir ".\scripts\step6_team_role_context.py"              "--input $step5 --output $step6" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 7 - Rank Props"             $NBADir ".\scripts\step7_rank_props.py"                     "--input $step6 --output $step7" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 8 - Direction Context"      $NBADir (Join-Path $SportsRoot "NBA\scripts\step8_add_direction_context.py") "--input $step7 --sheet ALL --output $step8Csv --xlsx $step8Xlsx --date $Date" }

    if ($ok -and (Test-Path $step8Xlsx)) {
        try {
            Copy-Item $step8Xlsx $datedOut -Force -ErrorAction Stop
            Write-Host "  [$tagLower] Dated copy -> $datedOut" -ForegroundColor DarkGray
        }
        catch {
            Write-Host "  [$tagLower] WARN: could not copy dated slate to $datedOut : $_" -ForegroundColor Yellow
        }
    }
    if ($ok) { Write-Host "  $tagLower complete." -ForegroundColor Green } else { Write-Host "  $tagLower FAILED." -ForegroundColor Red }
    return $ok
}

# -- Helper: copy a clean slate output into dated outputs\<date>\ --------------
function Copy-DatedSlateOutput {
    param(
        [string]$SourcePath,
        [string]$DatedFileName,
        [string]$Label
    )
    if (-not (Test-Path $SourcePath)) { return }
    try {
        if (-not (Test-Path $OutDir)) {
            New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
        }
        $datedPath = Join-Path $OutDir $DatedFileName
        Copy-Item -LiteralPath $SourcePath -Destination $datedPath -Force -ErrorAction Stop
        Write-Host "  [$Label] Dated copy -> $datedPath" -ForegroundColor DarkGray
    }
    catch {
        Write-Host "  [$Label] WARN: failed dated copy from $SourcePath" -ForegroundColor Yellow
    }
}

# -- step7b edge model scoring (non-fatal if model missing or script errors) ---
function Invoke-PropOracleStep7b {
    param([string]$SportLabel)
    Push-Location $Root
    try {
        $sp = Join-Path $Root "scripts\step7b_edge_score.py"
        if (-not (Test-Path $sp)) {
            Write-Host "  [$SportLabel] step7b: WARN (missing scripts\step7b_edge_score.py)" -ForegroundColor Yellow
            return
        }
        $cmd = "py -3.14 `"$sp`" --sport `"$SportLabel`""
        Write-Host "  --> step7b ($SportLabel)" -ForegroundColor Yellow
        Write-Host "        CMD: $cmd" -ForegroundColor DarkGray
        $output = Invoke-Expression $cmd 2>&1
        $exit   = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "  [$SportLabel] step7b: WARN (exit $exit)" -ForegroundColor Yellow
        } else {
            Write-Host "  [$SportLabel] step7b: OK" -ForegroundColor Green
        }
    } catch {
        Write-Host "  [$SportLabel] step7b: WARN (exit 1)" -ForegroundColor Yellow
    } finally {
        Pop-Location
    }
}

# -- Helper: git push templates -----------------------------------------------
function Run-GitPush {
    Write-Host ""
    Write-Host "[ GIT ] Pushing updated templates to GitHub..." -ForegroundColor Cyan
    Push-Location $Root
    try {
        git add "ui_runner/templates/tickets_latest.html" `
                "ui_runner/templates/tickets_latest.json" `
                "ui_runner/templates/slate_latest.json" 2>&1 | Out-Null
        $msg       = "chore: pipeline update $Date $(Get-Date -Format 'HH:mm')"
        $commitOut = git commit -m $msg 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pushOut = git push origin main 2>&1
            foreach ($line in $pushOut) { Write-Host "    $line" -ForegroundColor DarkGray }
            Write-Host "  OK - Pushed to GitHub" -ForegroundColor Green
            "$Date $(Get-Date -Format 'HH:mm:ss') - PUSHED: $msg" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        } else {
            Write-Host "  (no changes to push)" -ForegroundColor DarkGray
            "$Date $(Get-Date -Format 'HH:mm:ss') - NO CHANGES" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        }
    } catch {
        Write-Host "  Git push failed: $_" -ForegroundColor Yellow
        "$Date $(Get-Date -Format 'HH:mm:ss') - PUSH FAILED: $_" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
    } finally {
        Pop-Location
    }
}

function Run-GitPushGradeArtifacts {
    param(
        [string]$GradeDate,
        # Pipeline "today" slate: stage ticket_eval_<this>.html alongside yesterday's graded bundle
        # so Grades hub date pills match /tickets before the next morning's grader run.
        [string]$AlsoTicketEvalDate = ""
    )

    Write-Host ""
    Write-Host "[ GIT ] Pushing grade HTML for slate date $GradeDate ..." -ForegroundColor Cyan
    $candidates = @(
        "ui_runner/templates/slate_eval_$GradeDate.html",
        "ui_runner/templates/ticket_eval_$GradeDate.html",
        "ui_runner/templates/graded_props_$GradeDate.json"
    )
    if ($AlsoTicketEvalDate -and ($AlsoTicketEvalDate -ne $GradeDate) -and ($AlsoTicketEvalDate -match '^\d{4}-\d{2}-\d{2}$')) {
        $candidates += "ui_runner/templates/ticket_eval_$AlsoTicketEvalDate.html"
    }
    $toStage = @()
    foreach ($rel in $candidates) {
        $full = Join-Path $Root ($rel -replace "/", "\")
        if (Test-Path $full) { $toStage += $rel }
    }
    if (-not $toStage.Count) {
        Write-Host "  No slate_eval / ticket_eval / graded_props found for $GradeDate — nothing to push" -ForegroundColor DarkGray
        "$Date $(Get-Date -Format 'HH:mm:ss') - GRADE PUSH SKIP (no grade artifacts for $GradeDate)" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        return
    }

    Push-Location $Root
    try {
        foreach ($f in $toStage) {
            git add $f 2>&1 | Out-Null
            Write-Host "    staged: $f" -ForegroundColor DarkGray
        }
        $msg       = "chore: grades $GradeDate $(Get-Date -Format 'HH:mm')"
        git commit -m $msg 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $pushOut = git push origin main 2>&1
            foreach ($line in $pushOut) { Write-Host "    $line" -ForegroundColor DarkGray }
            Write-Host "  OK - Grade HTML pushed" -ForegroundColor Green
            "$Date $(Get-Date -Format 'HH:mm:ss') - GRADE PUSHED: $msg" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        } else {
            Write-Host "  No git changes for grade HTML (already committed?)" -ForegroundColor DarkGray
            $unpushed = git log origin/main..HEAD --oneline 2>&1
            if ($unpushed) {
                git push origin main 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Host "  OK - Flushed pending commits" -ForegroundColor Green }
            }
        }
    } catch {
        Write-Host "  Grade push exception: $_" -ForegroundColor Red
    } finally {
        Pop-Location
    }
}

# Run-PostPipelineGrader is defined in run_post_pipeline_grader.ps1 (dot-sourced above).

# -- Alt-book fetch (Underdog + DraftKings); failures are non-fatal for combined ----------
function Invoke-AltBookPy {
    param(
        [string]$Label,
        [string]$RelScript,
        [string]$Arguments
    )
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Root
    try {
        $cmd = "py -3.14 `"$RelScript`" $Arguments"
        Write-Host "        CMD: $cmd" -ForegroundColor DarkGray
        $output = Invoke-Expression $cmd 2>&1
        $exit   = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      [alt-books] WARN exit $exit (continuing)" -ForegroundColor Yellow
        } else {
            Write-Host "      OK" -ForegroundColor Green
        }
    } catch {
        Write-Host "      [alt-books] WARN: $_" -ForegroundColor Yellow
    } finally {
        Pop-Location
    }
}

function Invoke-AltBookFetches {
    if (-not $UseAltBooks) {
        Write-Host "  [alt-books] Skipped (PrizePicks-only mode; pass -UseAltBooks to enable)" -ForegroundColor DarkGray
        return
    }
    if ($SkipAltBooks) {
        Write-Host "  [alt-books] Skipped (-SkipAltBooks with -UseAltBooks)" -ForegroundColor DarkGray
        return
    }
    $UdScript    = Join-Path $Root "scripts\fetch_underdog_pickem.py"
    $DkScript    = Join-Path $Root "scripts\fetch_draftkings_player_props.py"
    $MergeScript = Join-Path $Root "scripts\merge_draftkings_pickem_csvs.py"
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

    Write-Host "  [alt-books] Fetching Underdog + DraftKings for cross-book columns..." -ForegroundColor Cyan
    $UdOut = Join-Path $OutDir "underdog_props.csv"
    if (Test-Path $UdScript) {
        Invoke-AltBookPy "Underdog pick'em (ALL sports)" ".\scripts\fetch_underdog_pickem.py" "--sport ALL --output `"$UdOut`" --min-rows 0"
    } else {
        Write-Host "  [alt-books] WARN missing scripts\fetch_underdog_pickem.py" -ForegroundColor Yellow
    }

    $dkFiles = [System.Collections.Generic.List[string]]::new()
    if (Test-Path $DkScript) {
        foreach ($row in @(
            @{ league = "nba"; name = "dk_props_nba.csv" },
            @{ league = "nhl"; name = "dk_props_nhl.csv" },
            @{ league = "mlb"; name = "dk_props_mlb.csv" },
            @{ league = "cbb"; name = "dk_props_cbb.csv" }
        )) {
            $part = Join-Path $OutDir $row.name
            Invoke-AltBookPy "DraftKings $($row.league.ToUpper())" ".\scripts\fetch_draftkings_player_props.py" "--league $($row.league) -o `"$part`""
            if (Test-Path $part) { [void]$dkFiles.Add($part) }
        }
        $DkAll = Join-Path $OutDir "draftkings_props_all.csv"
        if ($dkFiles.Count -gt 0 -and (Test-Path $MergeScript)) {
            $inList = ($dkFiles | ForEach-Object { "`"$_`"" }) -join " "
            Invoke-AltBookPy "Merge DraftKings CSVs" ".\scripts\merge_draftkings_pickem_csvs.py" "--inputs $inList -o `"$DkAll`""
        } elseif ($dkFiles.Count -gt 0 -and -not (Test-Path $MergeScript)) {
            Write-Host "  [alt-books] WARN missing merge_draftkings_pickem_csvs.py — using first league file only" -ForegroundColor Yellow
            Copy-Item $dkFiles[0] $DkAll -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "  [alt-books] WARN missing scripts\fetch_draftkings_player_props.py" -ForegroundColor Yellow
    }
}

# -- Helper: run combined, auto-detect all sports on disk ---------------------
function Run-Combined {
    param([string]$Reason = "")
    if ($SkipCombined) {
        Write-Host "  [pipeline] Skipping combined (-SkipCombined)" -ForegroundColor DarkGray
        return $true
    }
    if ($script:CombinedRanThisSession) {
        Write-Host "  [pipeline] Skipping duplicate Run-Combined call" -ForegroundColor DarkGray
        return $true
    }
    $script:CombinedRanThisSession = $true
    Write-Host ""
    $label = if ($Reason) { "[ COMBINED SLATE -- $Reason ]" } else { "[ COMBINED SLATE ]" }
    Write-Host $label -ForegroundColor Magenta
    Write-Host ""

    # Clean up any stale root-level combined_slate_tickets files from previous runs
    Get-ChildItem -Path $Root -Filter "combined_slate_tickets_*.xlsx" | Remove-Item -Force -ErrorAction SilentlyContinue

    Write-Host "  [combined] Step8 inputs: auto-resolve in combined_slate_tickets.py (outputs\$Date\ + Sports\...)" -ForegroundColor DarkGray

    Invoke-AltBookFetches

    $CombinedOut  = Join-Path $Root "combined_slate_tickets_$Date.xlsx"
    $CombinedArgs = ""

    if ($UseAltBooks -and -not $SkipAltBooks) {
        $UdCsv = Join-Path $OutDir "underdog_props.csv"
        $DkAll = Join-Path $OutDir "draftkings_props_all.csv"
        $DkNba = Join-Path $OutDir "draftkings_props_nba.csv"
        if (Test-Path $UdCsv) {
            $CombinedArgs += " --underdog-csv `"$UdCsv`""
            Write-Host "  [alt-books] Passing Underdog CSV" -ForegroundColor DarkGray
        }
        if (Test-Path $DkAll) {
            $CombinedArgs += " --draftkings-csv `"$DkAll`""
            Write-Host "  [alt-books] Passing DraftKings merged CSV" -ForegroundColor DarkGray
        } elseif (Test-Path $DkNba) {
            $CombinedArgs += " --draftkings-csv `"$DkNba`""
            Write-Host "  [alt-books] Passing DraftKings NBA CSV" -ForegroundColor DarkGray
        }
    }

    # Keep strict date checks for NBA-family slates so /tickets never shows yesterday as today.
    $CombinedArgs += " --date $Date --allow-cross-date-fallback --output `"$CombinedOut`" --tiers A,B,C,D --min-hit-rate 0.45 --min-edge -0.25 --max-tickets 40 --ticket-gen-starts 64 --nba-structured-variants 8 --ticket-candidate-sort rule --prioritize-ticket-hit --write-web --merge-web-latest --web-outdir `"$WebOutDir`""
    if (-not $WebEvOnly) {
        $CombinedArgs += " --no-web-ev-gate"
    }
    $CombinedArgs = $CombinedArgs.Trim()

    $okC = Run-Step "Combined Slate + Tickets" $Root ".\scripts\combined_slate_tickets.py" $CombinedArgs

    if ($okC) {
        $datedCombinedPath = Join-Path $OutDir "combined_slate_tickets_$Date.xlsx"
        # REMOVED: HHmmss snapshot caused resolver ambiguity in build_ticket_eval.py.
        # No downstream consumer used this file. Use --slate override if a specific
        # snapshot is needed for debugging.
        Copy-Item $CombinedOut $datedCombinedPath -Force -ErrorAction SilentlyContinue
        $toGradeTomorrowPath = Join-Path $OutDir "combined_slate_tickets_${Date}_to_grade_tomorrow.xlsx"
        Copy-Item $CombinedOut $toGradeTomorrowPath -Force -ErrorAction SilentlyContinue
        $canonicalCombinedPath = Join-Path $CanonicalOutDir "combined_slate_tickets_$Date.xlsx"
        $canonicalFrozenPath = Join-Path $CanonicalOutDir "combined_slate_tickets_${Date}_to_grade_tomorrow.xlsx"
        Copy-Item $CombinedOut $canonicalCombinedPath -Force -ErrorAction SilentlyContinue
        Copy-Item $CombinedOut $canonicalFrozenPath -Force -ErrorAction SilentlyContinue
        # Snapshot today's tickets_latest.json into ui_runner/data as dated JSON source.
        $TicketsLatestJson = Join-Path $WebOutDir "tickets_latest.json"
        $DatedTicketsJson  = Join-Path $UiDataDir "combined_slate_tickets_$Date.json"
        if (Test-Path $TicketsLatestJson) {
            if (Test-Path $DatedTicketsJson) {
                $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
                $backupPath = Join-Path $UiDataBackupsDir "combined_slate_tickets_${Date}.bak_$stamp.json"
                Copy-Item $DatedTicketsJson $backupPath -Force -ErrorAction SilentlyContinue
            }
            Copy-Item $TicketsLatestJson $DatedTicketsJson -Force -ErrorAction SilentlyContinue
            Write-Host "  Saved -> $DatedTicketsJson" -ForegroundColor Green
        } else {
            Write-Host "  [warn] Missing tickets_latest.json; skipped dated JSON snapshot for ML backfill." -ForegroundColor Yellow
        }
        # Canonical platform UI snapshots (templates consumed by web app).
        foreach ($uiName in @(
            "tickets_latest.html",
            "tickets_latest.json",
            "slate_latest.json",
            "ticket_eval_$Date.html",
            "slate_eval_$Date.html",
            "graded_props_$Date.json"
        )) {
            $uiSrc = Join-Path $WebOutDir $uiName
            $uiDst = Join-Path $CanonicalPlatformUiDir $uiName
            if (Test-Path $uiSrc) {
                Copy-Item $uiSrc $uiDst -Force -ErrorAction SilentlyContinue
                Write-Host "  Saved -> $uiDst" -ForegroundColor Green
            }
        }
        # Mobile bundle: must follow combined + template writes so slate_latest / tickets_latest match.
        $bundleOk = Invoke-PropOracleMobileBundle
        if (-not $bundleOk) {
            Write-Host "  [mobile] WARN: generate_mobile_bundle.py failed — mobile/www may be stale." -ForegroundColor Yellow
        }
        # Canonical mobile app snapshots (bundled mobile/www artifacts).
        foreach ($mobileName in @(
            "index.html",
            "tickets.html",
            "grades.html",
            "income.html",
            "payout.html",
            "slate_latest.json",
            "tickets_latest.json",
            "pipeline_status.json",
            "slate_display_date.json"
        )) {
            $mobileSrc = Join-Path $MobileWwwDir $mobileName
            $mobileDst = Join-Path $CanonicalMobileAppDir $mobileName
            if (Test-Path $mobileSrc) {
                Copy-Item $mobileSrc $mobileDst -Force -ErrorAction SilentlyContinue
                Write-Host "  Saved -> $mobileDst" -ForegroundColor Green
            }
        }
        # REMOVED: depended on HHmmss snapshot (see above). Re-implement using
        # tickets_latest.json diff if count-compare is needed in future.

        Remove-Item $CombinedOut -Force -ErrorAction SilentlyContinue
        Write-Host "  Saved -> $datedCombinedPath" -ForegroundColor Green
        Write-Host "  Saved -> $toGradeTomorrowPath" -ForegroundColor Green
        Write-Host "  Saved -> $canonicalCombinedPath" -ForegroundColor Green
        Write-Host "  Saved -> $canonicalFrozenPath" -ForegroundColor Green
        if ($RunPayoutEngine) {
            Write-Host "[PAYOUT ENGINE] Fetching exact multipliers from PrizePicks..." -ForegroundColor Magenta
            try {
                Push-Location $Root
                $payoutOut = py -3.14 ".\scripts\fetch_prizepicks_payouts.py" --date $Date 2>&1
                $payoutExit = $LASTEXITCODE
                foreach ($line in $payoutOut) { Write-Host "    $line" -ForegroundColor DarkGray }
                if ($payoutExit -ne 0) {
                    Write-Host "[PAYOUT ENGINE] WARN: exited $payoutExit" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "[PAYOUT ENGINE] WARN: $_" -ForegroundColor Yellow
            } finally {
                Pop-Location
            }
        }
        if ($SkipPush) {
            Write-Host "  [git] Skipping push (-SkipPush)" -ForegroundColor DarkGray
        } else {
            Run-GitPush
        }
        try {
            Run-PostPipelineGrader
        } catch {
            Write-Host "[PostGrader] WARN: $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Combined FAILED" -ForegroundColor Red
    }
    Write-Host ""
    return $okC
}

# -- Helper: print elapsed + done banner --------------------------------------
function Print-Done {
    $Elapsed = (Get-Date) - $StartTime
    Write-Host "======================================================" -ForegroundColor Cyan
    Write-Host ("  DONE  -- Elapsed: {0}" -f $Elapsed.ToString("mm\:ss")) -ForegroundColor Cyan
    Write-Host "======================================================" -ForegroundColor Cyan
    Write-Host ""
}

# =============================================================================
#  COMBINED ONLY  -- picks up every sport already on disk
# =============================================================================
if ($CombinedOnly) {
    Run-Combined "from existing outputs"
    Print-Done
    exit
}

# =============================================================================
#  WNBA ONLY
# =============================================================================
if ($WNBAOnly) {
    Write-Host "[ WNBA PIPELINE ]" -ForegroundColor Magenta
    Write-Host "  Delegating to scripts\run_wnba_pipeline.ps1 ..." -ForegroundColor DarkGray
    $wnbaPs1 = Join-Path $Root "scripts\run_wnba_pipeline.ps1"
    if (-not (Test-Path -LiteralPath $wnbaPs1)) {
        Write-Host "  ERROR: WNBA runner not found: $wnbaPs1" -ForegroundColor Red
        exit 1
    }
    $wnbaInvoke = @{ Date = $Date }
    # WNBA cache wipe only via scripts\run_wnba_pipeline.ps1 -RefreshCache (not NBA -RefreshCache).
    if ($SkipFetch) { $wnbaInvoke["SkipFetch"] = $true }
    if ($WNBACdp) { $wnbaInvoke["Cdp"] = $WNBACdp }
    & $wnbaPs1 @wnbaInvoke
    $wnbaOk = ($LASTEXITCODE -eq 0)
    if ($wnbaOk) {
        Run-Combined "after WNBA"
    } else {
        Write-Host "  [WNBA] Skipping combined (pipeline reported failure)." -ForegroundColor Yellow
    }
    Print-Done
    if (-not $wnbaOk) { exit 1 }
    exit
}

# =============================================================================
#  NFL ONLY
# =============================================================================
if ($NFLOnly) {
    Write-Host "[ NFL PIPELINE ]" -ForegroundColor Magenta
    $nflPs1 = Join-Path $Root "scripts\run_nfl_pipeline.ps1"
    if (-not (Test-Path -LiteralPath $nflPs1)) {
        Write-Host "  ERROR: NFL runner not found: $nflPs1" -ForegroundColor Red
        exit 1
    }
    if ($SkipFetch) {
        & $nflPs1 -Date $Date -SkipFetch
    } else {
        & $nflPs1 -Date $Date
    }
    $nflOk = ($LASTEXITCODE -eq 0)
    if ($nflOk) {
        Run-Combined "after NFL"
    }
    Print-Done
    exit
}

# =============================================================================
#  NHL ONLY
# =============================================================================
if ($NHLOnly) {
    Write-Host "[ NHL PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"         "--output `"$NHLRunOutDir\step1_nhl_props.csv`"" } } else { Write-Host "  [NHL] Skipping step1 fetch -- using existing $NHLRunOutDir\step1_nhl_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input `"$NHLRunOutDir\step1_nhl_props.csv`" --output `"$NHLRunOutDir\step2_nhl_picktypes.csv`"" }
    if ($ok) { $ok = Run-Step "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input `"$NHLRunOutDir\step2_nhl_picktypes.csv`" --output `"$NHLRunOutDir\step3_nhl_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input `"$NHLRunOutDir\step3_nhl_with_defense.csv`" --output `"$NHLRunOutDir\step4_nhl_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input `"$NHLRunOutDir\step4_nhl_with_stats.csv`" --output `"$NHLRunOutDir\step5_nhl_hit_rates.csv`" --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input `"$NHLRunOutDir\step5_nhl_hit_rates.csv`" --output `"$NHLRunOutDir\step6_nhl_role_context.csv`"" }
    if ($ok) { $ok = Run-Step "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input `"$NHLRunOutDir\step6_nhl_role_context.csv`" --output `"$NHLRunOutDir\step7_nhl_ranked.xlsx`"" }
    if ($ok) { Invoke-PropOracleStep7b "NHL" }
    if ($ok) { $ok = Run-Step "NHL Step 8 - Direction Context"  $NHLDir (Join-Path $SportsRoot "NHL\scripts\step8_add_direction_context_nhl.py")  "--input `"$NHLRunOutDir\step7_nhl_ranked.xlsx`" --output `"$NHLRunOutDir\step8_nhl_direction_clean.xlsx`" --date $Date" }
    Write-Host ""
    if ($ok) { Write-Host "  NHL complete." -ForegroundColor Green } else { Write-Host "  NHL FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after NHL" }
    Print-Done
    exit
}

# =============================================================================
#  MLB ONLY
# =============================================================================
if ($MLBOnly) {
    Write-Host "[ MLB PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) {
        Clear-MLBGeneratedOutputs -BaseDir $MLBRunOutDir
        if ($ok) { $ok = Invoke-MLBStep1Fetch -WorkDir $MLBDir -PipelineDate $Date -OutputPath "$MLBRunOutDir\step1_mlb_props.csv" }
        if ($ok) {
            $mlbStep1Health = Get-MLBStep1DateHealth -CsvPath (Join-Path $MLBRunOutDir "step1_mlb_props.csv") -TargetDate $Date
            if (-not $mlbStep1Health.ok) {
                Write-Host "  [MLB] Step1 date health failed ($($mlbStep1Health.reason)); clearing MLB outputs to avoid stale carry-over." -ForegroundColor Yellow
                Clear-MLBGeneratedOutputs -BaseDir $MLBRunOutDir
                $ok = $false
            }
        }
    } else {
        Write-Host "  [MLB] Skipping step1 fetch -- using existing $MLBRunOutDir\step1_mlb_props.csv" -ForegroundColor DarkGray
    }
    if ($ok) { $ok = Run-Step "MLB Step 2 - Attach Pick Types"  $MLBDir ".\scripts\step2_attach_picktypes_mlb.py"       "--input `"$MLBRunOutDir\step1_mlb_props.csv`" --output `"$MLBRunOutDir\step2_mlb_picktypes.csv`" --id_lookup_timeout_s 6 --id_lookup_retries 2 --id_lookup_budget_s 180" }
    if ($ok) { $ok = Run-Step "MLB Step 3 - Attach Defense"     $MLBDir ".\scripts\step3_attach_defense_mlb.py"         "--input `"$MLBRunOutDir\step2_mlb_picktypes.csv`" --defense mlb_defense_summary.csv --output `"$MLBRunOutDir\step3_mlb_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step "MLB Step 4 - Player Stats"       $MLBDir ".\scripts\step4_attach_player_stats_mlb.py"    "--input `"$MLBRunOutDir\step3_mlb_with_defense.csv`" --cache mlb_stats_cache.csv --output `"$MLBRunOutDir\step4_mlb_with_stats.csv`" --season $MLBSeasonYear" -TimeoutSeconds 1200 }
    if ($ok -and $MLBVerify) {
        Write-Host ""
        Write-Host "  [MLBVerify] Step 1-4 completed. Health summary:" -ForegroundColor Cyan
        foreach ($name in @("step1_mlb_props.csv", "step2_mlb_picktypes.csv", "step3_mlb_with_defense.csv", "step4_mlb_with_stats.csv")) {
            $p = Join-Path $MLBRunOutDir $name
            if (Test-Path $p) {
                $rows = 0
                try { $rows = (Import-Csv -Path $p).Count } catch { $rows = -1 }
                $rowsText = if ($rows -ge 0) { "$rows rows" } else { "rows=unreadable" }
                Write-Host "    OK  $name  ($rowsText)" -ForegroundColor DarkGray
            } else {
                Write-Host "    MISS $name" -ForegroundColor Yellow
            }
        }
        Write-Host ""
        Write-Host "  MLB verify complete (stopped after Step 4)." -ForegroundColor Green
        Print-Done
        exit
    }
    if ($ok) { $ok = Run-Step "MLB Step 5 - Line Hit Rates"     $MLBDir ".\scripts\step5_add_line_hit_rates_mlb.py"     "--input `"$MLBRunOutDir\step4_mlb_with_stats.csv`" --output `"$MLBRunOutDir\step5_mlb_hit_rates.csv`"" }
    if ($ok) { $ok = Run-Step "MLB Step 6 - Team Role Context"  $MLBDir ".\scripts\step6_team_role_context_mlb.py"      "--input `"$MLBRunOutDir\step5_mlb_hit_rates.csv`" --output `"$MLBRunOutDir\step6_mlb_role_context.csv`"" }
    if ($ok) { $ok = Run-Step "MLB Step 7 - Rank Props"         $MLBDir ".\scripts\step7_rank_props_mlb.py"             "--input `"$MLBRunOutDir\step6_mlb_role_context.csv`" --output `"$MLBRunOutDir\step7_mlb_ranked.xlsx`"" }
    if ($ok) { Invoke-PropOracleStep7b "MLB" }
    if ($ok) { $ok = Run-Step "MLB Step 8 - Direction Context"  $MLBDir (Join-Path $SportsRoot "MLB\scripts\step8_add_direction_context_mlb.py")  "--input `"$MLBRunOutDir\step7_mlb_ranked.xlsx`" --output `"$MLBRunOutDir\step8_mlb_direction.csv`" --xlsx `"$MLBRunOutDir\step8_mlb_direction_clean.xlsx`" --date $Date" }
    Write-Host ""
    if ($ok) { Write-Host "  MLB complete." -ForegroundColor Green } else { Write-Host "  MLB FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after MLB" }
    Print-Done
    exit
}

# =============================================================================
#  SOCCER ONLY
# =============================================================================
if ($SoccerOnly) {
    Write-Host "[ SOCCER PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output `"$SoccerRunOutDir\step1_soccer_props.csv`" --date $Date" } } else { Write-Host "  [Soccer] Skipping step1 fetch -- using existing $SoccerRunOutDir\step1_soccer_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input `"$SoccerRunOutDir\step1_soccer_props.csv`" --output `"$SoccerRunOutDir\step2_soccer_picktypes.csv`"" }
    if ($ok) {
        if ($SkipDefenseRefresh) {
            Write-Host "  [Soccer] Skipping defense refresh (-SkipDefenseRefresh) — using cache\\soccer_defense_summary.csv" -ForegroundColor DarkGray
        } else {
            $ok = Run-Step "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv"
        }
    }
    if ($ok) { $ok = Run-Step "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input `"$SoccerRunOutDir\step2_soccer_picktypes.csv`" --defense cache\soccer_defense_summary.csv --output `"$SoccerRunOutDir\step3_soccer_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input `"$SoccerRunOutDir\step3_soccer_with_defense.csv`" --output `"$SoccerRunOutDir\step4_soccer_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input `"$SoccerRunOutDir\step4_soccer_with_stats.csv`" --output `"$SoccerRunOutDir\step5_soccer_hit_rates.csv`" --compute10" }
    if ($ok) { $ok = Run-Step "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input `"$SoccerRunOutDir\step5_soccer_hit_rates.csv`" --output `"$SoccerRunOutDir\step6_soccer_role_context.csv`"" }
    if ($ok) { $ok = Run-Step "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input `"$SoccerRunOutDir\step6_soccer_role_context.csv`" --output `"$SoccerRunOutDir\step7_soccer_ranked.xlsx`"" }
    if ($ok) { Invoke-PropOracleStep7b "Soccer" }
    if ($ok) { $ok = Run-Step "Soccer Step 8 - Direction Context"  $SoccerDir (Join-Path $SportsRoot "Soccer\scripts\step8_add_direction_context_soccer.py")  "--input `"$SoccerRunOutDir\step7_soccer_ranked.xlsx`" --sheet ALL --output `"$SoccerRunOutDir\step8_soccer_direction.csv`" --xlsx `"$SoccerRunOutDir\step8_soccer_direction_clean.xlsx`" --date $Date" }
    Write-Host ""
    if ($ok) { Write-Host "  Soccer complete." -ForegroundColor Green } else { Write-Host "  Soccer FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after Soccer" }
    Print-Done
    exit
}

# =============================================================================
#  TENNIS ONLY  (steps 1-8 + step7b)
# =============================================================================
if ($TennisOnly) {
    Write-Host "[ TENNIS PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  [Tennis] Slate day (step8 filter): $TennisDate ET  |  Bundle folder: outputs\$Date" -ForegroundColor DarkGray
    Write-Host "  [Tennis] Step1 loads the full PrizePicks tennis board (often spans several days). Step8 --date keeps only rows for $TennisDate." -ForegroundColor DarkGray
    $ok = $true
    if (-not $SkipFetch) {
        if ($ok) { $ok = Run-Step "Tennis Step 1 - Fetch PrizePicks" $TennisDir ".\scripts\step1_fetch_prizepicks_tennis.py" "--league_id 5 --output `"$TennisRunOutDir\step1_tennis_props.csv`"" }
    } else {
        Write-Host "  [Tennis] Skipping step1 fetch -- using existing $TennisRunOutDir\step1_tennis_props.csv" -ForegroundColor DarkGray
    }
    if ($ok) { $ok = Run-Step "Tennis Step 2 - Attach Pick Types" $TennisDir ".\scripts\step2_attach_picktypes_tennis.py" "--input `"$TennisRunOutDir\step1_tennis_props.csv`" --output `"$TennisRunOutDir\step2_tennis_picktypes.csv`"" }
    if ($ok) { $ok = Run-Step "Tennis Step 3 - Defense Stub" $TennisDir ".\scripts\step3_defense_rankings_tennis.py" "--input `"$TennisRunOutDir\step2_tennis_picktypes.csv`" --output `"$TennisRunOutDir\step3_tennis_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step "Tennis Step 4 - Player Stats + History" $TennisDir ".\scripts\step4_attach_player_stats_tennis.py" "--input `"$TennisRunOutDir\step3_tennis_with_defense.csv`" --output `"$TennisRunOutDir\step4_tennis_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step "Tennis Step 5 - Hit Rates" $TennisDir ".\scripts\step5_compute_hitrates_tennis.py" "--input `"$TennisRunOutDir\step4_tennis_with_stats.csv`" --output `"$TennisRunOutDir\step5_tennis_hit_rates.csv`" --compute10" }
    if ($ok) { $ok = Run-Step "Tennis Step 6 - Context" $TennisDir ".\scripts\step6_add_context_tennis.py" "--input `"$TennisRunOutDir\step5_tennis_hit_rates.csv`" --output `"$TennisRunOutDir\step6_tennis_role_context.csv`"" }
    if ($ok) { $ok = Run-Step "Tennis Step 7 - Rank Props" $TennisDir ".\scripts\step7_rank_props_tennis.py" "--input `"$TennisRunOutDir\step6_tennis_role_context.csv`" --output `"$TennisRunOutDir\step7_tennis_ranked.xlsx`"" }
    if ($ok) { Invoke-PropOracleStep7b "Tennis" }
    if ($ok) { $ok = Run-Step "Tennis Step 8 - Direction Context" $TennisDir (Join-Path $SportsRoot "Tennis\scripts\step8_add_direction_context_tennis.py") "--input `"$TennisRunOutDir\step7_tennis_ranked.xlsx`" --sheet ALL --output `"$TennisRunOutDir\step8_tennis_direction.csv`" --xlsx `"$TennisRunOutDir\step8_tennis_direction_clean.xlsx`" --date $TennisDate" }
    if ($ok) {
        Copy-DatedSlateOutput `
            -SourcePath (Join-Path $TennisRunOutDir "step8_tennis_direction_clean.xlsx") `
            -DatedFileName "step8_tennis_direction_clean_$TennisDate.xlsx" `
            -Label "Tennis"
    }
    Write-Host ""
    if ($ok) { Write-Host "  Tennis complete." -ForegroundColor Green } else { Write-Host "  Tennis FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after Tennis" }
    Print-Done
    exit
}

# =============================================================================
#  CFB ONLY  (College Football — CBB-style steps 1-6)
# =============================================================================
if ($CFBOnly) {
    Write-Host "[ CFB PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "CFB Step 1 - Fetch PrizePicks"      $CFBDir ".\scripts\pipeline\step1_pp_cfb_scraper.py"      "--out `"$CFBRunOutDir\step1_cfb.csv`"" } } else { Write-Host "  [CFB] Skipping step1 fetch -- using existing $CFBRunOutDir\step1_cfb.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "CFB Step 2 - Normalize"               $CFBDir ".\scripts\pipeline\step2_normalize.py"                            "--input `"$CFBRunOutDir\step1_cfb.csv`" --output `"$CFBRunOutDir\step2_cfb.csv`"" }
    if ($ok) { $ok = Run-Step "CFB Step 3a - Build Unit Rankings"      $CFBDir ".\scripts\build_cfb_unit_rankings.py"                             "--season $CFBSeasonYear --out data\reference\cfb_team_unit_rankings.csv" }
    if ($ok) { $ok = Run-Step "CFB Step 3b - Attach Pass/Run Ranks"    $CFBDir ".\scripts\pipeline\step3_attach_unit_rankings.py"               "--input `"$CFBRunOutDir\step2_cfb.csv`" --rankings data\reference\cfb_team_unit_rankings.csv --output `"$CFBRunOutDir\step3_with_unit_rankings_cfb.csv`"" }
    if ($ok) { $ok = Run-Step "CFB Step 4 - Attach ESPN IDs"         $CFBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input `"$CFBRunOutDir\step3_with_unit_rankings_cfb.csv`" --output `"$CFBRunOutDir\step3_cfb.csv`" --master data/reference/ncaa_football_athletes_master.csv" }
    if ($ok) { $ok = Run-Step "CFB Step 5 - Boxscore Stats"          $CFBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input `"$CFBRunOutDir\step3_cfb.csv`" --output `"$CFBRunOutDir\step5b_cfb.csv`" --date $Date --days 200 --cache data\cache\cfb_boxscore_cache.csv" }
    if ($ok) { $ok = Run-Step "CFB Step 6 - Rank Props"              $CFBDir ".\scripts\pipeline\step6_rank_props_cfb.py"                       "--input `"$CFBRunOutDir\step5b_cfb.csv`" --output `"$CFBRunOutDir\step6_ranked_cfb.xlsx`" --cache data\cache\cfb_boxscore_cache.csv" }
    if ($ok) { Invoke-PropOracleStep7b "CFB" }
    Write-Host ""
    if ($ok) { Write-Host "  CFB complete." -ForegroundColor Green } else { Write-Host "  CFB FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after CFB" }
    Print-Done
    exit
}

# =============================================================================
#  CBB ONLY
# =============================================================================
if ($CBBOnly) {
    Write-Host "[ CBB PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "CBB Step 1 - Fetch PrizePicks"      $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py"      "--out `"$CBBRunOutDir\step1_cbb.csv`"" } } else { Write-Host "  [CBB] Skipping step1 fetch -- using existing $CBBRunOutDir\step1_cbb.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input `"$CBBRunOutDir\step1_cbb.csv`" --output `"$CBBRunOutDir\step2_cbb.csv`"" }
    if ($ok) { $ok = Run-Step "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input `"$CBBRunOutDir\step2_cbb.csv`" --defense data\reference\cbb_def_rankings.csv --output `"$CBBRunOutDir\step3b_with_def_rankings_cbb.csv`"" }
    if ($ok) { $ok = Run-Step "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input `"$CBBRunOutDir\step3b_with_def_rankings_cbb.csv`" --output `"$CBBRunOutDir\step3_cbb.csv`" --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input `"$CBBRunOutDir\step3_cbb.csv`" --output `"$CBBRunOutDir\step5b_cbb.csv`"" }
    if ($ok) { $ok = Run-Step "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input `"$CBBRunOutDir\step5b_cbb.csv`" --output `"$CBBRunOutDir\step6_ranked_cbb.xlsx`"" }
    if ($ok) { Invoke-PropOracleStep7b "CBB" }
    Write-Host ""
    if ($ok) { Write-Host "  CBB complete." -ForegroundColor Green } else { Write-Host "  CBB FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after CBB" }
    Print-Done
    exit
}

# =============================================================================
#  NBA ONLY
# =============================================================================
if ($NBAOnly) {
    Write-Host "[ NBA PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""

    if (Test-Path (Join-Path $NBADir "RUN_COMPLETE.flag")) { Remove-Item (Join-Path $NBADir "RUN_COMPLETE.flag") -Force }

    if ($RefreshCache) {
        Write-Host "  [Cache] Wiping ESPN cache files..." -ForegroundColor Yellow
        Remove-Item (Join-Path $NBADir "nba_espn_boxscore_cache.csv") -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $NBADir "nba_to_espn_id_map.csv")      -Force -ErrorAction SilentlyContinue
        Write-Host "  [Cache] Done." -ForegroundColor Green
        Write-Host ""
    } else {
        Check-AutoRefreshCache
    }

    $ok = $true
    $nbaStep1Solo = Join-Path $NBARunOutDir "step1_pp_props_today.csv"
    if (-not $SkipFetch) {
        if ($ok) {
            $ok = Run-Step "NBA Step 1 - Fetch PrizePicks" $NBADir ".\scripts\step1_fetch_prizepicks_api.py" `
                "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output `"$nbaStep1Solo`" --date $Date --allow-nearest-future"
        }
    } else {
        Write-Host "  [NBA] Skipping step1 fetch -- using existing $nbaStep1Solo" -ForegroundColor DarkGray
        $nbaHealth = Get-NBAStep1DateHealth -CsvPath $nbaStep1Solo -TargetDate $Date
        if (-not $nbaHealth.ok) {
            $legacy = Join-Path $NBADir "data\outputs\step1_pp_props_today.csv"
            $legHealth = Get-NBAStep1DateHealth -CsvPath $legacy -TargetDate $Date
            if ($legHealth.ok) {
                Copy-Item -LiteralPath $legacy -Destination $nbaStep1Solo -Force
                Write-Host "  [NBA] Synced step1 from legacy data/outputs" -ForegroundColor Cyan
            } else {
                Write-Host "  [NBA] step1 unhealthy ($($nbaHealth.reason)) — emergency fetch" -ForegroundColor Yellow
                $ok = Invoke-NBAStep1Fetch -WorkDir $NBADir -PipelineDate $Date -OutputPath $nbaStep1Solo
            }
        }
    }
    $nbaHealthGate = Get-NBAStep1DateHealth -CsvPath $nbaStep1Solo -TargetDate $Date
    if (-not $nbaHealthGate.ok) {
        Write-Host "  [NBA] Aborting: no valid step1 for $Date ($($nbaHealthGate.reason))" -ForegroundColor Red
        $ok = $false
    }
    if ($ok) { $ok = Run-Step "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input `"$nbaStep1Solo`" --output `"$NBARunOutDir\step2_with_picktypes.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input `"$NBARunOutDir\step2_with_picktypes.csv`" --defense data\cache\defense_team_summary.csv --output `"$NBARunOutDir\step3_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate `"$NBARunOutDir\step3_with_defense.csv`" --out `"$NBARunOutDir\step4_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input `"$NBARunOutDir\step4_with_stats.csv`" --output `"$NBARunOutDir\step5_with_hit_rates.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input `"$NBARunOutDir\step5_with_hit_rates.csv`" --output `"$NBARunOutDir\step6_with_team_role_context.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input `"$NBARunOutDir\step6_with_team_role_context.csv`" --output `"$NBARunOutDir\step6a_with_opp_stats.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input `"$NBARunOutDir\step6a_with_opp_stats.csv`" --output `"$NBARunOutDir\step6b_with_game_context.csv`" --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input `"$NBARunOutDir\step6b_with_game_context.csv`" --output `"$NBARunOutDir\step6c_with_schedule_flags.csv`" --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input `"$NBARunOutDir\step6c_with_schedule_flags.csv`" --output `"$NBARunOutDir\step6d_with_h2h.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input `"$NBARunOutDir\step6d_with_h2h.csv`" --output `"$NBARunOutDir\step7_ranked_props.xlsx`"" }
    if ($ok) { Invoke-PropOracleStep7b "NBA" }
    if ($ok) { $ok = Run-Step "NBA Step 8 - Direction Context"       $NBADir (Join-Path $SportsRoot "NBA\scripts\step8_add_direction_context.py")         "--input `"$NBARunOutDir\step7_ranked_props.xlsx`" --sheet ALL --output `"$NBARunOutDir\step8_all_direction.csv`" --date $Date" }
    if ($ok) {
        $nbaMainStep8 = Join-Path $NBARunOutDir "step8_all_direction_clean.xlsx"
        if (Test-Path $nbaMainStep8) {
            Copy-DatedSlateOutput -SourcePath $nbaMainStep8 -DatedFileName "step8_nba_direction_clean_$Date.xlsx" -Label "NBA"
        }
    }
    if ($ok) { $ok = Run-NBAPeriodPipeline -Tag "nba1h" -LeagueId "84"  -SkipFetchStep:$SkipFetch }
    if ($ok) { $ok = Run-NBAPeriodPipeline -Tag "nba1q" -LeagueId "192" -SkipFetchStep:$SkipFetch }

    if ($ok) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }
    Write-Host ""
    if ($ok) { Write-Host "  NBA complete." -ForegroundColor Green } else { Write-Host "  NBA FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after NBA" }
    Print-Done
    exit
}

# =============================================================================
#  FULL PARALLEL RUN  (NBA + CBB + NHL + Soccer + MLB + NFL [+ WNBA when in season])
# =============================================================================
if ($RefreshCache) {
    Write-Host "  [Cache] Wiping ESPN cache files..." -ForegroundColor Yellow
    Remove-Item (Join-Path $NBADir "nba_espn_boxscore_cache.csv") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $NBADir "nba_to_espn_id_map.csv")      -Force -ErrorAction SilentlyContinue
    Write-Host "  [Cache] Done." -ForegroundColor Green
    Write-Host ""
} else {
    Check-AutoRefreshCache
}

if (Test-Path (Join-Path $NBADir "RUN_COMPLETE.flag")) { Remove-Item (Join-Path $NBADir "RUN_COMPLETE.flag") -Force }

# -- Backfill boxscore DB for last 3 days (all sports) ------------------------
Write-Host "[ DB BACKFILL ]" -ForegroundColor Cyan
Write-Host "  Syncing proporacle_ref.db for last 3 days..." -ForegroundColor DarkGray
$backfillScript = Join-Path $NBADir "scripts\build_boxscore_ref.py"
if (Test-Path $backfillScript) {
    $backfillOut = Invoke-Expression "py -3.14 `"$backfillScript`" --backfill --days 3 --sports nba nhl soccer" 2>&1
    foreach ($line in $backfillOut) { Write-Host "  $line" -ForegroundColor DarkGray }
    Write-Host "  DB backfill complete." -ForegroundColor Green
} else {
    Write-Host "  WARNING: build_boxscore_ref.py not found -- skipping backfill" -ForegroundColor Yellow
}
Write-Host ""

$wnbaParallel = ($ForceWNBA.IsPresent -or ($Date -ge $WNBA_SEASON_START))
if (-not $wnbaParallel) {
    Write-Host "  [WNBA] Parallel job skipped until $WNBA_SEASON_START (use -ForceWNBA to run early)." -ForegroundColor DarkGray
}

# Men's CBB: no expected slate on/after 2026-04-07 (align with scripts\run_daily.ps1 Get-MissingTodaySlateOutputs).
$CBB_PARALLEL_ACTIVE = ($Date -lt "2026-04-07")
if (-not $CBB_PARALLEL_ACTIVE) {
    Write-Host "  [CBB] Parallel job skipped (men's season ended; date >= 2026-04-07)." -ForegroundColor DarkGray
}

# NFL PrizePicks-style board: run Aug–Feb only (off-season roughly Mar–Jul). Adjust if PP adds a summer slate.
$NFL_PARALLEL_ACTIVE = $true
try {
    $dNfl = [datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
    $NFL_PARALLEL_ACTIVE = ($dNfl.Month -ge 8 -or $dNfl.Month -le 2)
} catch { }
if (-not $NFL_PARALLEL_ACTIVE) {
    Write-Host "  [NFL] Parallel job skipped (off-season for $Date; active months Aug–Feb)." -ForegroundColor DarkGray
}

# College Football (PrizePicks league_id=15): Aug–Jan regular + bowls.
$CFB_PARALLEL_ACTIVE = $true
try {
    $dCfb = [datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
    $CFB_PARALLEL_ACTIVE = ($dCfb.Month -ge 8 -or $dCfb.Month -le 1)
} catch { }
if (-not $CFB_PARALLEL_ACTIVE) {
    Write-Host "  [CFB] Parallel job skipped (off-season for $Date; active months Aug–Jan)." -ForegroundColor DarkGray
}

$parallelLabel = if ($wnbaParallel) {
    "[ PARALLEL PIPELINE: NBA + CBB + CFB + NHL + Soccer + Tennis + MLB + NFL + WNBA ]"
} else {
    "[ PARALLEL PIPELINE: NBA + CBB + CFB + NHL + Soccer + Tennis + MLB + NFL ]"
}
Write-Host $parallelLabel -ForegroundColor Magenta
Write-Host ""
Write-Host "  Starting all pipelines simultaneously..." -ForegroundColor Cyan
Write-Host ""

# -- NBA Job ------------------------------------------------------------------
$NBAJob = Start-Job -ScriptBlock {
    param($NBADir, $Date, $OddsApiKey, $SkipFetch, $RepoRoot, $NBARunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[NBA] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[NBA] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[NBA] OK: $Label"; return $true
        } catch { Write-Output "[NBA] EXCEPTION in $Label`: $_"; return $false
        } finally { Pop-Location }
    }
    function Get-Step1DateHealth-Job {
        param([string]$CsvPath, [string]$TargetDate)
        if (-not (Test-Path $CsvPath)) { return @{ ok = $false; reason = "missing_file" } }
        try { $rows = Import-Csv -Path $CsvPath } catch { return @{ ok = $false; reason = "read_error" } }
        if (-not $rows -or $rows.Count -eq 0) { return @{ ok = $false; reason = "empty_file" } }
        $match = @()
        if ($rows[0].PSObject.Properties.Name -contains "game_date") {
            $match = $rows | Where-Object { (($_.game_date | ForEach-Object { "$_".Trim() })) -eq $TargetDate }
        } elseif ($rows[0].PSObject.Properties.Name -contains "start_time") {
            $match = $rows | Where-Object { "$($_.start_time)".Length -ge 10 -and "$($_.start_time)".Substring(0, 10) -eq $TargetDate }
        } else {
            return @{ ok = $false; reason = "missing_date_columns" }
        }
        $reason = if ($match.Count -gt 0) { "ok" } else { "date_mismatch" }
        return @{ ok = ($match.Count -gt 0); reason = $reason }
    }
    function Invoke-NBAStep1Fetch-Job {
        param([string]$Dir, [string]$PipelineDate, [string]$OutputPath)
        Write-Output "[NBA] --> NBA Step 1 - Fetch PrizePicks (emergency / recovery)"
        Push-Location $Dir
        try {
            $outDir = Split-Path -Parent $OutputPath
            if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
                New-Item -ItemType Directory -Force -Path $outDir | Out-Null
            }
            $output = & py -3.14 ".\scripts\step1_fetch_prizepicks_api.py" `
                --league_id 7 --game_mode pickem --per_page 250 --max_pages 5 `
                --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 `
                --replace --date $PipelineDate --allow-nearest-future `
                --output $OutputPath 2>&1
            $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[NBA] FAILED: NBA Step 1 (exit $exit)"; return $false }
            Write-Output "[NBA] OK: NBA Step 1"; return $true
        } catch {
            Write-Output "[NBA] EXCEPTION: NBA Step 1: $_"; return $false
        } finally {
            Pop-Location
        }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    $ok = $true
    $nbaStep1 = Join-Path $NBARunOutDir "step1_pp_props_today.csv"
    if (-not $SkipFetch) {
        if ($ok) {
            $ok = Run-Step-Job "NBA Step 1 - Fetch PrizePicks" $NBADir ".\scripts\step1_fetch_prizepicks_api.py" `
                "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output `"$nbaStep1`" --date $Date --allow-nearest-future"
        }
    } else {
        Write-Output "[NBA] Skipping step1 fetch"
        $health = Get-Step1DateHealth-Job -CsvPath $nbaStep1 -TargetDate $Date
        if (-not $health.ok) {
            $legacy = Join-Path $NBADir "data\outputs\step1_pp_props_today.csv"
            $legHealth = Get-Step1DateHealth-Job -CsvPath $legacy -TargetDate $Date
            if ($legHealth.ok) {
                Copy-Item -LiteralPath $legacy -Destination $nbaStep1 -Force
                Write-Output "[NBA] Synced step1 from legacy data/outputs -> $nbaStep1"
            } else {
                Write-Output "[NBA] step1 unhealthy ($($health.reason)) — emergency fetch"
                $ok = Invoke-NBAStep1Fetch-Job -Dir $NBADir -PipelineDate $Date -OutputPath $nbaStep1
            }
        }
    }
    $health = Get-Step1DateHealth-Job -CsvPath $nbaStep1 -TargetDate $Date
    if (-not $health.ok) {
        Write-Output "[NBA] Aborting steps 2-8: no valid step1 for $Date ($($health.reason))"
        $ok = $false
    }
    if ($ok) { $ok = Run-Step-Job "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input `"$nbaStep1`" --output `"$NBARunOutDir\step2_with_picktypes.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input `"$NBARunOutDir\step2_with_picktypes.csv`" --defense data\cache\defense_team_summary.csv --output `"$NBARunOutDir\step3_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate `"$NBARunOutDir\step3_with_defense.csv`" --out `"$NBARunOutDir\step4_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input `"$NBARunOutDir\step4_with_stats.csv`" --output `"$NBARunOutDir\step5_with_hit_rates.csv`" --compute10" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input `"$NBARunOutDir\step5_with_hit_rates.csv`" --output `"$NBARunOutDir\step6_with_team_role_context.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input `"$NBARunOutDir\step6_with_team_role_context.csv`" --output `"$NBARunOutDir\step6a_with_opp_stats.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input `"$NBARunOutDir\step6a_with_opp_stats.csv`" --output `"$NBARunOutDir\step6b_with_game_context.csv`" --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input `"$NBARunOutDir\step6b_with_game_context.csv`" --output `"$NBARunOutDir\step6c_with_schedule_flags.csv`" --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input `"$NBARunOutDir\step6c_with_schedule_flags.csv`" --output `"$NBARunOutDir\step6d_with_h2h.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input `"$NBARunOutDir\step6d_with_h2h.csv`" --output `"$NBARunOutDir\step7_ranked_props.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "NBA" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "NBA Step 8 - Direction Context"       $NBADir (Join-Path $RepoRoot "Sports\NBA\scripts\step8_add_direction_context.py")         "--input `"$NBARunOutDir\step7_ranked_props.xlsx`" --sheet ALL --output `"$NBARunOutDir\step8_all_direction.csv`" --date $Date" }
    return $ok
} -ArgumentList $NBADir, $Date, $OddsApiKey, $SkipFetch, $Root, $NBARunOutDir

# -- CBB Job ------------------------------------------------------------------
$CBBJob = $null
if ($CBB_PARALLEL_ACTIVE) {
$CBBJob = Start-Job -ScriptBlock {
    param($CBBDir, $Date, $SkipFetch, $RepoRoot, $CBBRunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[CBB] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[CBB] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[CBB] OK: $Label"; return $true
        } catch { Write-Output "[CBB] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "CBB Step 1 - Fetch PrizePicks"      $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py"      "--out `"$CBBRunOutDir\step1_cbb.csv`"" } } else { Write-Output "[CBB] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input `"$CBBRunOutDir\step1_cbb.csv`" --output `"$CBBRunOutDir\step2_cbb.csv`"" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input `"$CBBRunOutDir\step2_cbb.csv`" --defense data\reference\cbb_def_rankings.csv --output `"$CBBRunOutDir\step3b_with_def_rankings_cbb.csv`"" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input `"$CBBRunOutDir\step3b_with_def_rankings_cbb.csv`" --output `"$CBBRunOutDir\step3_cbb.csv`" --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input `"$CBBRunOutDir\step3_cbb.csv`" --output `"$CBBRunOutDir\step5b_cbb.csv`"" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input `"$CBBRunOutDir\step5b_cbb.csv`" --output `"$CBBRunOutDir\step6_ranked_cbb.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "CBB" $RepoRoot }
    return $ok
} -ArgumentList $CBBDir, $Date, $SkipFetch, $Root, $CBBRunOutDir
}

# -- CFB Job ------------------------------------------------------------------
$CFBJob = $null
if ($CFB_PARALLEL_ACTIVE) {
$CFBJob = Start-Job -ScriptBlock {
    param($CFBDir, $Date, $SkipFetch, $RepoRoot, $CFBRunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    try {
        $cfbSeason = ([datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)).Year
    } catch {
        $cfbSeason = (Get-Date).Year
    }
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[CFB] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[CFB] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[CFB] OK: $Label"; return $true
        } catch { Write-Output "[CFB] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "CFB Step 1 - Fetch PrizePicks"      $CFBDir ".\scripts\pipeline\step1_pp_cfb_scraper.py"      "--out `"$CFBRunOutDir\step1_cfb.csv`"" } } else { Write-Output "[CFB] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "CFB Step 2 - Normalize"               $CFBDir ".\scripts\pipeline\step2_normalize.py"                            "--input `"$CFBRunOutDir\step1_cfb.csv`" --output `"$CFBRunOutDir\step2_cfb.csv`"" }
    if ($ok) { $ok = Run-Step-Job "CFB Step 3a - Build Unit Rankings"      $CFBDir ".\scripts\build_cfb_unit_rankings.py"                             "--season $cfbSeason --out data\reference\cfb_team_unit_rankings.csv" }
    if ($ok) { $ok = Run-Step-Job "CFB Step 3b - Attach Pass/Run Ranks"    $CFBDir ".\scripts\pipeline\step3_attach_unit_rankings.py"               "--input `"$CFBRunOutDir\step2_cfb.csv`" --rankings data\reference\cfb_team_unit_rankings.csv --output `"$CFBRunOutDir\step3_with_unit_rankings_cfb.csv`"" }
    if ($ok) { $ok = Run-Step-Job "CFB Step 4 - Attach ESPN IDs"         $CFBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input `"$CFBRunOutDir\step3_with_unit_rankings_cfb.csv`" --output `"$CFBRunOutDir\step3_cfb.csv`" --master data/reference/ncaa_football_athletes_master.csv" }
    if ($ok) { $ok = Run-Step-Job "CFB Step 5 - Boxscore Stats"          $CFBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input `"$CFBRunOutDir\step3_cfb.csv`" --output `"$CFBRunOutDir\step5b_cfb.csv`" --date $Date --days 200 --cache data\cache\cfb_boxscore_cache.csv" }
    if ($ok) { $ok = Run-Step-Job "CFB Step 6 - Rank Props"              $CFBDir ".\scripts\pipeline\step6_rank_props_cfb.py"                       "--input `"$CFBRunOutDir\step5b_cfb.csv`" --output `"$CFBRunOutDir\step6_ranked_cfb.xlsx`" --cache data\cache\cfb_boxscore_cache.csv" }
    if ($ok) { Invoke-Step7b-Job "CFB" $RepoRoot }
    return $ok
} -ArgumentList $CFBDir, $Date, $SkipFetch, $Root, $CFBRunOutDir
}

# -- NHL Job ------------------------------------------------------------------
$NHLJob = Start-Job -ScriptBlock {
    param($NHLDir, $SkipFetch, $RepoRoot, $Date, $NHLRunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[NHL] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[NHL] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[NHL] OK: $Label"; return $true
        } catch { Write-Output "[NHL] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"        "--output `"$NHLRunOutDir\step1_nhl_props.csv`"" } } else { Write-Output "[NHL] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input `"$NHLRunOutDir\step1_nhl_props.csv`" --output `"$NHLRunOutDir\step2_nhl_picktypes.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input `"$NHLRunOutDir\step2_nhl_picktypes.csv`" --output `"$NHLRunOutDir\step3_nhl_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input `"$NHLRunOutDir\step3_nhl_with_defense.csv`" --output `"$NHLRunOutDir\step4_nhl_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input `"$NHLRunOutDir\step4_nhl_with_stats.csv`" --output `"$NHLRunOutDir\step5_nhl_hit_rates.csv`" --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input `"$NHLRunOutDir\step5_nhl_hit_rates.csv`" --output `"$NHLRunOutDir\step6_nhl_role_context.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input `"$NHLRunOutDir\step6_nhl_role_context.csv`" --output `"$NHLRunOutDir\step7_nhl_ranked.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "NHL" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "NHL Step 8 - Direction Context"  $NHLDir (Join-Path $RepoRoot "Sports\NHL\scripts\step8_add_direction_context_nhl.py")  "--input `"$NHLRunOutDir\step7_nhl_ranked.xlsx`" --output `"$NHLRunOutDir\step8_nhl_direction_clean.xlsx`" --date $Date" }
    return $ok
} -ArgumentList $NHLDir, $SkipFetch, $Root, $Date, $NHLRunOutDir

# -- Soccer Job ---------------------------------------------------------------
$SoccerJob = Start-Job -ScriptBlock {
    param($SoccerDir, $Date, $SkipFetch, $RepoRoot, $SkipDefenseRefresh, $SoccerRunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[SOCCER] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[SOCCER] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[SOCCER] OK: $Label"; return $true
        } catch { Write-Output "[SOCCER] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output `"$SoccerRunOutDir\step1_soccer_props.csv`" --date $Date" } } else { Write-Output "[Soccer] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input `"$SoccerRunOutDir\step1_soccer_props.csv`" --output `"$SoccerRunOutDir\step2_soccer_picktypes.csv`"" }
    if ($ok) {
        if ($SkipDefenseRefresh) {
            Write-Output "[Soccer] Skipping defense refresh (-SkipDefenseRefresh)"
        } else {
            $ok = Run-Step-Job "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv"
        }
    }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input `"$SoccerRunOutDir\step2_soccer_picktypes.csv`" --defense cache\soccer_defense_summary.csv --output `"$SoccerRunOutDir\step3_soccer_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input `"$SoccerRunOutDir\step3_soccer_with_defense.csv`" --output `"$SoccerRunOutDir\step4_soccer_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input `"$SoccerRunOutDir\step4_soccer_with_stats.csv`" --output `"$SoccerRunOutDir\step5_soccer_hit_rates.csv`" --compute10" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input `"$SoccerRunOutDir\step5_soccer_hit_rates.csv`" --output `"$SoccerRunOutDir\step6_soccer_role_context.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input `"$SoccerRunOutDir\step6_soccer_role_context.csv`" --output `"$SoccerRunOutDir\step7_soccer_ranked.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "Soccer" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 8 - Direction Context"  $SoccerDir (Join-Path $RepoRoot "Sports\Soccer\scripts\step8_add_direction_context_soccer.py")  "--input `"$SoccerRunOutDir\step7_soccer_ranked.xlsx`" --sheet ALL --output `"$SoccerRunOutDir\step8_soccer_direction.csv`" --xlsx `"$SoccerRunOutDir\step8_soccer_direction_clean.xlsx`" --date $Date" }
    return $ok
} -ArgumentList $SoccerDir, $Date, $SkipFetch, $Root, [bool]$SkipDefenseRefresh, $SoccerRunOutDir

# -- Tennis Job ---------------------------------------------------------------
$TennisJob = Start-Job -ScriptBlock {
    param($TennisDir, $TennisDate, $SkipFetch, $RepoRoot, $TennisRunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[TENNIS] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[TENNIS] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[TENNIS] OK: $Label"; return $true
        } catch { Write-Output "[TENNIS] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    Write-Output "[TENNIS] Step8 filters to ET date $TennisDate; step1 loads full PrizePicks tennis board (may include several calendar days)"
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "Tennis Step 1 - Fetch PrizePicks" $TennisDir ".\scripts\step1_fetch_prizepicks_tennis.py" "--league_id 5 --output `"$TennisRunOutDir\step1_tennis_props.csv`"" } } else { Write-Output "[Tennis] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 2 - Attach Pick Types" $TennisDir ".\scripts\step2_attach_picktypes_tennis.py" "--input `"$TennisRunOutDir\step1_tennis_props.csv`" --output `"$TennisRunOutDir\step2_tennis_picktypes.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 3 - Defense Stub" $TennisDir ".\scripts\step3_defense_rankings_tennis.py" "--input `"$TennisRunOutDir\step2_tennis_picktypes.csv`" --output `"$TennisRunOutDir\step3_tennis_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 4 - Player Stats + History" $TennisDir ".\scripts\step4_attach_player_stats_tennis.py" "--input `"$TennisRunOutDir\step3_tennis_with_defense.csv`" --output `"$TennisRunOutDir\step4_tennis_with_stats.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 5 - Hit Rates" $TennisDir ".\scripts\step5_compute_hitrates_tennis.py" "--input `"$TennisRunOutDir\step4_tennis_with_stats.csv`" --output `"$TennisRunOutDir\step5_tennis_hit_rates.csv`" --compute10" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 6 - Context" $TennisDir ".\scripts\step6_add_context_tennis.py" "--input `"$TennisRunOutDir\step5_tennis_hit_rates.csv`" --output `"$TennisRunOutDir\step6_tennis_role_context.csv`"" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 7 - Rank Props" $TennisDir ".\scripts\step7_rank_props_tennis.py" "--input `"$TennisRunOutDir\step6_tennis_role_context.csv`" --output `"$TennisRunOutDir\step7_tennis_ranked.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "Tennis" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 8 - Direction Context" $TennisDir (Join-Path $RepoRoot "Sports\Tennis\scripts\step8_add_direction_context_tennis.py") "--input `"$TennisRunOutDir\step7_tennis_ranked.xlsx`" --sheet ALL --output `"$TennisRunOutDir\step8_tennis_direction.csv`" --xlsx `"$TennisRunOutDir\step8_tennis_direction_clean.xlsx`" --date $TennisDate" }
    return $ok
} -ArgumentList $TennisDir, $TennisDate, $SkipFetch, $Root, $TennisRunOutDir

# -- MLB Job ------------------------------------------------------------------
# MLB activated April 2026
$MLBJob = Start-Job -ScriptBlock {
    param($MLBDir, $Date, $SkipFetch, $RepoRoot, $MLBRunOutDir, $MlbSeasonYear)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[MLB] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[MLB] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[MLB] OK: $Label"; return $true
        } catch { Write-Output "[MLB] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-MLBStep1Fetch-Job {
        param([string]$Dir, [string]$PipelineDate, [string]$OutputPath)
        Write-Output "[MLB] --> MLB Step 1 - Fetch PrizePicks (direct API, then Playwright if needed)"
        Push-Location $Dir
        try {
            Write-Output "        CMD: py -3.14 -u .\scripts\step1_fetch_prizepicks_mlb.py --date $PipelineDate --output $OutputPath (direct API)"
            $output = & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
                --date $PipelineDate --output $OutputPath `
                --api-retries 2 --api-session-waves 1 `
                --api-wave-gap-min 8 --api-wave-gap-max 15 `
                --api-403-cooldown-after 2 --api-403-cooldown-seconds 20 `
                --api-403-cooldown-jitter-min 4 --api-403-cooldown-jitter-max 10 2>&1
            $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) {
                Write-Output "[MLB] Direct API failed (exit $exit); trying Playwright"
                Write-Output "        CMD: py -3.14 -u .\scripts\step1_fetch_prizepicks_mlb.py --playwright --date $PipelineDate --output $OutputPath"
                $output = & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
                    --playwright --timeout 120 --retries 1 --retry_delay 5 `
                    --date $PipelineDate --output $OutputPath 2>&1
                $exit = $LASTEXITCODE
                foreach ($line in $output) { Write-Output "        $line" }
            }
            if ($exit -ne 0) { Write-Output "[MLB] FAILED: MLB Step 1 (exit $exit)"; return $false }
            Write-Output "[MLB] OK: MLB Step 1"; return $true
        } catch {
            Write-Output "[MLB] EXCEPTION: $_"; return $false
        } finally {
            Pop-Location
        }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    function Get-MLBStep1DateHealth-Job {
        param([string]$CsvPath, [string]$TargetDate)
        if (-not (Test-Path $CsvPath)) { return @{ ok = $false; reason = "missing_file" } }
        try { $rows = Import-Csv -Path $CsvPath } catch { return @{ ok = $false; reason = "read_error" } }
        if (-not $rows -or $rows.Count -eq 0) { return @{ ok = $false; reason = "empty_file" } }
        $match = @()
        if ($rows[0].PSObject.Properties.Name -contains "game_date") {
            $match = $rows | Where-Object { (($_.game_date | ForEach-Object { "$_".Trim() })) -eq $TargetDate }
        } elseif ($rows[0].PSObject.Properties.Name -contains "start_time") {
            $match = $rows | Where-Object { "$($_.start_time)".Length -ge 10 -and "$($_.start_time)".Substring(0, 10) -eq $TargetDate }
        } else {
            return @{ ok = $false; reason = "missing_date_columns" }
        }
        $reason = if ($match.Count -gt 0) { "ok" } else { "date_mismatch" }
        return @{ ok = ($match.Count -gt 0); reason = $reason }
    }
    function Clear-MLBGeneratedOutputs-Job {
        param([string]$BaseDir)
        foreach ($p in @(
            "step1_mlb_props.csv",
            "step2_mlb_picktypes.csv",
            "step3_mlb_with_defense.csv",
            "step4_mlb_with_stats.csv",
            "step5_mlb_hit_rates.csv",
            "step6_mlb_role_context.csv",
            "step7_mlb_ranked.xlsx",
            "step8_mlb_direction.csv",
            "step8_mlb_direction_clean.xlsx"
        )) {
            Remove-Item (Join-Path $BaseDir $p) -Force -ErrorAction SilentlyContinue
        }
    }
    $ok = $true
    if (-not $SkipFetch) {
        Clear-MLBGeneratedOutputs-Job -BaseDir $MLBRunOutDir
        if ($ok) { $ok = Invoke-MLBStep1Fetch-Job -Dir $MLBDir -PipelineDate $Date -OutputPath "$MLBRunOutDir\step1_mlb_props.csv" }
        if ($ok) {
            $health = Get-MLBStep1DateHealth-Job -CsvPath (Join-Path $MLBRunOutDir "step1_mlb_props.csv") -TargetDate $Date
            if (-not $health.ok) {
                Write-Output "[MLB] Step1 date health failed ($($health.reason)); clearing MLB outputs to avoid stale carry-over."
                Clear-MLBGeneratedOutputs-Job -BaseDir $MLBRunOutDir
                $ok = $false
            }
        }
    } else {
        Write-Output "[MLB] Skipping step1 fetch"
        $mlbStep1 = Join-Path $MLBRunOutDir "step1_mlb_props.csv"
        $health = Get-MLBStep1DateHealth-Job -CsvPath $mlbStep1 -TargetDate $Date
        if (-not $health.ok) {
            $legacy = Join-Path $MLBDir "data\outputs\step1_mlb_props.csv"
            $legHealth = Get-MLBStep1DateHealth-Job -CsvPath $legacy -TargetDate $Date
            if ($legHealth.ok) {
                Copy-Item -LiteralPath $legacy -Destination $mlbStep1 -Force
                Write-Output "[MLB] Synced step1 from legacy data/outputs -> $mlbStep1"
            } else {
                Write-Output "[MLB] step1 unhealthy ($($health.reason)) — emergency fetch"
                $ok = Invoke-MLBStep1Fetch-Job -Dir $MLBDir -PipelineDate $Date -OutputPath $mlbStep1
            }
        }
    }
    $mlbStep1Check = Join-Path $MLBRunOutDir "step1_mlb_props.csv"
    $mlbHealth = Get-MLBStep1DateHealth-Job -CsvPath $mlbStep1Check -TargetDate $Date
    if (-not $mlbHealth.ok) {
        Write-Output "[MLB] Aborting steps 2-8: no valid step1 for $Date ($($mlbHealth.reason))"
        $ok = $false
    }
    if ($ok) { $ok = Run-Step-Job "MLB Step 2 - Attach Pick Types"  $MLBDir ".\scripts\step2_attach_picktypes_mlb.py"       "--input `"$MLBRunOutDir\step1_mlb_props.csv`" --output `"$MLBRunOutDir\step2_mlb_picktypes.csv`" --id_lookup_timeout_s 6 --id_lookup_retries 2 --id_lookup_budget_s 180" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 3 - Attach Defense"     $MLBDir ".\scripts\step3_attach_defense_mlb.py"         "--input `"$MLBRunOutDir\step2_mlb_picktypes.csv`" --defense mlb_defense_summary.csv --output `"$MLBRunOutDir\step3_mlb_with_defense.csv`"" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 4 - Player Stats"       $MLBDir ".\scripts\step4_attach_player_stats_mlb.py"    "--input `"$MLBRunOutDir\step3_mlb_with_defense.csv`" --cache mlb_stats_cache.csv --output `"$MLBRunOutDir\step4_mlb_with_stats.csv`" --season $MlbSeasonYear" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 5 - Line Hit Rates"     $MLBDir ".\scripts\step5_add_line_hit_rates_mlb.py"     "--input `"$MLBRunOutDir\step4_mlb_with_stats.csv`" --output `"$MLBRunOutDir\step5_mlb_hit_rates.csv`"" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 6 - Team Role Context"  $MLBDir ".\scripts\step6_team_role_context_mlb.py"      "--input `"$MLBRunOutDir\step5_mlb_hit_rates.csv`" --output `"$MLBRunOutDir\step6_mlb_role_context.csv`"" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 7 - Rank Props"         $MLBDir ".\scripts\step7_rank_props_mlb.py"             "--input `"$MLBRunOutDir\step6_mlb_role_context.csv`" --output `"$MLBRunOutDir\step7_mlb_ranked.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "MLB" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "MLB Step 8 - Direction Context"  $MLBDir (Join-Path $RepoRoot "Sports\MLB\scripts\step8_add_direction_context_mlb.py")  "--input `"$MLBRunOutDir\step7_mlb_ranked.xlsx`" --output `"$MLBRunOutDir\step8_mlb_direction.csv`" --xlsx `"$MLBRunOutDir\step8_mlb_direction_clean.xlsx`" --date $Date" }
    return $ok
} -ArgumentList $MLBDir, $Date, $SkipFetch, $Root, $MLBRunOutDir, $MLBSeasonYear

# -- WNBA Job (parallel full run from $WNBA_SEASON_START; optional -ForceWNBA) ---
$WNBAJob = $null
if ($wnbaParallel) {
    $WNBAJob = Start-Job -ScriptBlock {
        param($RepoRoot, $PipelineDate, $SkipFetchFlag, $WnbaCdp)
        $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
        $wnbaPs1 = Join-Path $RepoRoot "scripts\run_wnba_pipeline.ps1"
        if (-not (Test-Path -LiteralPath $wnbaPs1)) {
            Write-Output "[WNBA] ERROR missing runner: $wnbaPs1"
            return $false
        }
        Push-Location $RepoRoot
        try {
            $wnbaInvoke = @{ Date = $PipelineDate }
            # WNBA ESPN cache is independent of NBA -RefreshCache (do not wipe 6297-row backfill on full runs).
            if ($SkipFetchFlag) { $wnbaInvoke["SkipFetch"] = $true }
            if ($WnbaCdp) { $wnbaInvoke["Cdp"] = $WnbaCdp }
            & $wnbaPs1 @wnbaInvoke
            if ($LASTEXITCODE -ne 0) {
                Write-Output "[WNBA] WARN runner exit $LASTEXITCODE"
            }
            return ($LASTEXITCODE -eq 0)
        } catch {
            Write-Output "[WNBA] EXCEPTION: $_"
            return $false
        } finally {
            Pop-Location
        }
    } -ArgumentList $Root, $Date, [bool]$SkipFetch, $WNBACdp
}

# -- NFL Job ------------------------------------------------------------------
$NFLJob = $null
if ($NFL_PARALLEL_ACTIVE) {
$NFLJob = Start-Job -ScriptBlock {
    param($NFLDir, $Date, $SkipFetch, $RepoRoot, $DefenseSeason, $NFLRunOutDir)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    $env:NFL_PIPELINE_ACTIVE = "1"
    $nflOutD = Join-Path $NFLDir "outputs"
    if (-not (Test-Path $nflOutD)) {
        New-Item -ItemType Directory -Force -Path $nflOutD | Out-Null
    }
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[NFL] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[NFL] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[NFL] OK: $Label"; return $true
        } catch { Write-Output "[NFL] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    function Invoke-Step7b-Job {
        param([string]$SportLabel, [string]$R)
        Push-Location $R
        try {
            $p = Join-Path $R "scripts\step7b_edge_score.py"
            if (-not (Test-Path $p)) {
                Write-Output "  [$SportLabel] step7b: WARN (missing step7b_edge_score.py)"
                return
            }
            $cmd = "py -3.14 `"$p`" --sport `"$SportLabel`""
            Write-Output "  --> step7b ($SportLabel)"
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "  [$SportLabel] step7b: WARN (exit $exit)" } else { Write-Output "  [$SportLabel] step7b: OK" }
        } catch { Write-Output "  [$SportLabel] step7b: WARN (exit 1)" }
        finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NFL Step 1 - Fetch PrizePicks" $NFLDir ".\scripts\step1_fetch_prizepicks_nfl.py" "--output `"$NFLRunOutDir\step1_pp_props_today.csv`" --date $Date" } } else { Write-Output "[NFL] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NFL Step 2 - Clean Props" $NFLDir ".\scripts\step2_clean_props.py" "" }
    if ($ok) { $ok = Run-Step-Job "NFL Step 4 - Defense Rankings" $NFLDir ".\scripts\step4_defense_rankings.py" "--season $DefenseSeason --output data\defense_rankings.csv" }
    if ($ok) { $ok = Run-Step-Job "NFL Step 3 - Merge Defense" $NFLDir ".\scripts\step3_merge_defense_nfl.py" "" }
    if ($ok) { $ok = Run-Step-Job "NFL Step 6 - Hit Rates" $NFLDir ".\scripts\step6_historical_hit_rates.py" "" }
    if ($ok) { $ok = Run-Step-Job "NFL Step 7 - Rank Props" $NFLDir ".\scripts\step7_rank_props_nfl.py" "--output `"$NFLRunOutDir\step7_nfl_ranked.xlsx`"" }
    if ($ok) { Invoke-Step7b-Job "NFL" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "NFL Step 8 - Direction Context" $NFLDir ".\scripts\step8_add_direction_context_nfl.py" "--date $Date --output `"$NFLRunOutDir\step8_nfl_direction_clean.xlsx`"" }
    return $ok
} -ArgumentList $NFLDir, $Date, $SkipFetch, $Root, 2025, $NFLRunOutDir
}

# -- Wait + stream output -----------------------------------------------------
$allJobs = @($NBAJob, $CBBJob, $CFBJob, $NHLJob, $SoccerJob, $TennisJob, $MLBJob, $NFLJob, $WNBAJob) | Where-Object { $_ -ne $null }

Write-Host "  [Waiting for all pipelines to finish...]" -ForegroundColor DarkGray
Write-Host ""

$waitStart = Get-Date
$lastHeartbeat = $waitStart
$maxParallelMinutes = 45

while (($allJobs | Where-Object { $_.State -eq 'Running' }).Count -gt 0) {
    foreach ($job in $allJobs) {
        $out = Receive-Job $job -ErrorAction SilentlyContinue
        foreach ($line in $out) {
            Write-Host "    $line" -ForegroundColor DarkGray
        }
    }

    $now = Get-Date
    if ((New-TimeSpan -Start $lastHeartbeat -End $now).TotalSeconds -ge 30) {
        $states = $allJobs | ForEach-Object { "$($_.Name):$($_.State)" }
        Write-Host ("  [parallel status] " + ($states -join " | ")) -ForegroundColor DarkGray
        $lastHeartbeat = $now
    }

    if ((New-TimeSpan -Start $waitStart -End $now).TotalMinutes -ge $maxParallelMinutes) {
        Write-Host "  [parallel] Timeout waiting for jobs. Stopping remaining running jobs..." -ForegroundColor Yellow
        foreach ($rj in ($allJobs | Where-Object { $_.State -eq 'Running' })) {
            Write-Host "    stopping job $($rj.Name) ($($rj.Id))" -ForegroundColor Yellow
            Stop-Job -Job $rj -ErrorAction SilentlyContinue
        }
        break
    }

    Start-Sleep -Milliseconds 500
}

foreach ($job in $allJobs) {
    $out = Receive-Job $job -ErrorAction SilentlyContinue
    foreach ($line in $out) {
        Write-Host "    $line" -ForegroundColor DarkGray
    }
}

# -- Results ------------------------------------------------------------------
$NBASuccess    = Test-Path (Join-Path $NBARunOutDir "step8_all_direction_clean.xlsx")
$CBBSuccess    = if (-not $CBB_PARALLEL_ACTIVE) { $true } else { Test-Path (Join-Path $CBBRunOutDir "step6_ranked_cbb.xlsx") }
$CFBSuccess    = if (-not $CFB_PARALLEL_ACTIVE) { $true } else { Test-Path (Join-Path $CFBRunOutDir "step6_ranked_cfb.xlsx") }
$NHLSuccess    = Test-Path (Join-Path $NHLRunOutDir "step8_nhl_direction_clean.xlsx")
$SoccerSuccess = Test-Path (Join-Path $SoccerRunOutDir "step8_soccer_direction_clean.xlsx")
$MLBSuccess    = Test-Path (Join-Path $MLBRunOutDir "step8_mlb_direction_clean.xlsx")
$TennisSuccess = Test-Path (Join-Path $TennisRunOutDir "step8_tennis_direction_clean.xlsx")
$NFLSuccess    = if (-not $NFL_PARALLEL_ACTIVE) { $true } else { Test-Path (Join-Path $NFLRunOutDir "step8_nfl_direction_clean.xlsx") }
$WNBASuccess = $false
if ($wnbaParallel) {
    $wnbaStep8Clean = Join-Path $OutDir "wnba\step8_wnba_direction_clean.xlsx"
    $wnbaStep8Legacy = Join-Path $WNBADir "step8_wnba_direction_clean.xlsx"
    $WNBASuccess = (Test-Path -LiteralPath $wnbaStep8Clean) -or (Test-Path -LiteralPath $wnbaStep8Legacy)
}
$mlbStep1Health = Get-MLBStep1DateHealth -CsvPath (Join-Path $MLBRunOutDir "step1_mlb_props.csv") -TargetDate $Date
if (-not $mlbStep1Health.ok) {
    Write-Host "  [MLB] stale/invalid step1 for $Date ($($mlbStep1Health.reason)); clearing MLB outputs from this run." -ForegroundColor Yellow
    Clear-MLBGeneratedOutputs -BaseDir $MLBRunOutDir
    $MLBSuccess = $false
} else {
    $MLBSuccess = $MLBSuccess -and $true
}

$nbaStep1Health = Get-NBAStep1DateHealth -CsvPath (Join-Path $NBARunOutDir "step1_pp_props_today.csv") -TargetDate $Date
if (-not $nbaStep1Health.ok) {
    Write-Host "  [NBA] stale/invalid step1 for $Date ($($nbaStep1Health.reason)); clearing NBA outputs from this run." -ForegroundColor Yellow
    Clear-NBAGeneratedOutputs -BaseDir $NBARunOutDir
    $NBASuccess = $false
}

# Dated NBA main step8 for run_grader (avoids empty grades after the live workbook rolls to the next slate day).
$nbaMainStep8Parallel = Join-Path $NBARunOutDir "step8_all_direction_clean.xlsx"
if (Test-Path $nbaMainStep8Parallel) {
    Copy-DatedSlateOutput -SourcePath $nbaMainStep8Parallel -DatedFileName "step8_nba_direction_clean_$Date.xlsx" -Label "NBA"
}

# NBA period sub-slates are required by daily checks and combined defaults.
$NBA1HSuccess  = $false
$NBA1QSuccess  = $false
if ($NBASuccess) {
    $NBA1HSuccess = Run-NBAPeriodPipeline -Tag "nba1h" -LeagueId "84"  -SkipFetchStep:$SkipFetch
    $NBA1QSuccess = Run-NBAPeriodPipeline -Tag "nba1q" -LeagueId "192" -SkipFetchStep:$SkipFetch
}
$NBASuccess = $NBASuccess -and $NBA1HSuccess -and $NBA1QSuccess

if ($TennisSuccess) {
    Copy-DatedSlateOutput `
        -SourcePath (Join-Path $TennisRunOutDir "step8_tennis_direction_clean.xlsx") `
        -DatedFileName "step8_tennis_direction_clean_$TennisDate.xlsx" `
        -Label "Tennis"
}
# WNBA dated step8 mirror: scripts/run_wnba_pipeline.ps1 Publish-WnbaStep8CleanArtifacts (clean only).

Remove-Job $allJobs -Force -ErrorAction SilentlyContinue
if ($NBASuccess) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }

Write-Host ""
@(
    @{ Name="NBA";    Ok=$NBASuccess; Skip=$false },
    @{ Name="CBB";    Ok=$CBBSuccess; Skip=(-not $CBB_PARALLEL_ACTIVE) },
    @{ Name="CFB";    Ok=$CFBSuccess; Skip=(-not $CFB_PARALLEL_ACTIVE) },
    @{ Name="NHL";    Ok=$NHLSuccess; Skip=$false },
    @{ Name="Soccer"; Ok=$SoccerSuccess; Skip=$false },
    @{ Name="MLB";    Ok=$MLBSuccess; Skip=$false },
    @{ Name="NFL";    Ok=$NFLSuccess; Skip=(-not $NFL_PARALLEL_ACTIVE) }
) | ForEach-Object {
    if ($_.Skip) {
        Write-Host "  $($_.Name) skipped (off-season / not required)." -ForegroundColor DarkGray
    } elseif ($_.Ok) {
        Write-Host "  $($_.Name) complete." -ForegroundColor Green
    } else {
        Write-Host "  $($_.Name) FAILED."  -ForegroundColor Red
    }
}
if ($wnbaParallel) {
    if ($WNBASuccess) { Write-Host "  WNBA complete." -ForegroundColor Green }
    else { Write-Host "  WNBA FAILED." -ForegroundColor Red }
}

Run-Combined "full parallel run"
Print-Done
