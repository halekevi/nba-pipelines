# ============================================================
#  WNBA PROP PIPELINE  -  Run Script
#
#  Usage:
#    .\run_wnba_pipeline.ps1              # Full WNBA pipeline run
#    .\run_wnba_pipeline.ps1 -Date 2026-07-15   # Specify date
#    .\run_wnba_pipeline.ps1 -RefreshCache      # Wipe ESPN cache + rebuild
#    .\run_wnba_pipeline.ps1 -SkipFetch         # Use existing step1 output
#    .\run_wnba_pipeline.ps1 -Cdp http://127.0.0.1:9222   # PrizePicks via Chrome CDP (bypass DataDome)
#    .\run_wnba_pipeline.ps1 -PreferBrowser               # Use CDP when port 9222 is up (skip HTTP API)
#    .\run_wnba_pipeline.ps1 -CdpWhenListening              # Same as PreferBrowser if debug Chrome is on 9222
#    .\run_wnba_pipeline.ps1 -UsePlaywright             # Sports\WNBA\step1_fetch_prizepicks.py (profile browser)
#  DataDome: launch Chrome first: pwsh -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3
#    .\run_wnba_pipeline.ps1 -StatsFrom2025End           # Force step4 rolling stats through 2025-10-20 (overrides prior-season merge)
#    .\run_wnba_pipeline.ps1 -NoStatsFrom2025End         # Step4: current season only (no 2025 merge in cache)
#  Default step1: Sports\WNBA\step1_fetch_prizepicks.py (HTTP: warmup + chrome131 + session waves); NBA API script is fallback (chrome120).
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
    [switch]$UsePlaywright,
    [switch]$PreferBrowser,
    [switch]$CdpWhenListening,
    [switch]$NoCdpFallback,
    [switch]$HttpOnly,
    [switch]$StatsFrom2025End,
    [switch]$NoStatsFrom2025End,
    [switch]$Step1Only
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
if ($PreferBrowser -or $CdpWhenListening) {
    Write-Host "  [WNBA step1] Browser-first: CDP when debug port 9222 is reachable (skip HTTP)" -ForegroundColor DarkCyan
}
if ($HttpOnly) {
    Write-Host "  [WNBA step1] HttpOnly: no CDP fallback even if port 9222 is up" -ForegroundColor DarkYellow
}

function Get-CsvDataRowCount([string]$CsvPath) {
    if (-not (Test-Path -LiteralPath $CsvPath)) { return 0 }
    try {
        $raw = Import-Csv -LiteralPath $CsvPath
        if ($null -eq $raw) { return 0 }
        if ($raw -is [array]) { return $raw.Count }
        return 1
    }
    catch {
        return 0
    }
}

function Test-CdpEndpoint {
    param([string]$BaseUrl)
    try {
        $u = ($BaseUrl.TrimEnd("/")) + "/json/version"
        $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 4
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-WnbaStep1BrowserArgs {
    param([string]$OutCsv, [string]$SlateDate, [string]$CdpUrl)
    $a = @(
        "--league_id", "3",
        "--playwright",
        "--timeout", "120",
        "--game_mode", "pickem",
        "--per_page", "250",
        "--max_pages", "10",
        "--sleep", "1.2",
        "--cooldown_seconds", "90",
        "--max_cooldowns", "3",
        "--jitter_seconds", "10.0",
        "--output", $OutCsv,
        "--date", $SlateDate
    )
    if ($CdpUrl) {
        $a += @("--cdp", $CdpUrl)
    }
    return ($a -join " ")
}

function Invoke-WnbaStep1Browser {
    param([string]$CdpUrl, [string]$Label)
    $outCsv = Join-Path $WnbaRunOutDir "step1_wnba_props.csv"
    $browserArgs = Get-WnbaStep1BrowserArgs -OutCsv $outCsv -SlateDate $Date -CdpUrl $CdpUrl
    return (Run-Step $Label $WNBADir ".\step1_fetch_prizepicks.py" $browserArgs)
}

function Get-WnbaStep1HttpArgs {
    param([string]$OutCsv, [string]$SlateDate)
    return @(
        "--league_id", "3",
        "--game_mode", "pickem",
        "--per_page", "250",
        "--max_pages", "10",
        "--sleep", "2.0",
        "--cooldown_seconds", "90",
        "--max_cooldowns", "3",
        "--jitter_seconds", "10.0",
        "--max_403_retries", "5",
        "--first-page-waves", "3",
        "--output", $OutCsv,
        "--date", $SlateDate
    ) -join " "
}

function Invoke-WnbaStep1Http {
    param(
        [string]$Label,
        [string]$Impersonate = ""
    )
    $outCsv = Join-Path $WnbaRunOutDir "step1_wnba_props.csv"
    $savedImp = [string]$env:PROPORACLE_CURL_IMPERSONATE
    if ($Impersonate) {
        $env:PROPORACLE_CURL_IMPERSONATE = $Impersonate
    } elseif (-not $savedImp.Trim()) {
        $env:PROPORACLE_CURL_IMPERSONATE = "chrome131"
    }
    try {
        $httpArgs = Get-WnbaStep1HttpArgs -OutCsv $outCsv -SlateDate $Date
        return (Run-Step $Label $WNBADir ".\step1_fetch_prizepicks.py" $httpArgs)
    } finally {
        if ($Impersonate) {
            if ($savedImp) { $env:PROPORACLE_CURL_IMPERSONATE = $savedImp }
            else { Remove-Item Env:PROPORACLE_CURL_IMPERSONATE -ErrorAction SilentlyContinue }
        }
    }
}

function Invoke-WnbaStep1HttpNbaFallback {
    if (-not (Test-Path -LiteralPath $NbaApiStep1)) {
        Write-Host "  ERROR: NBA API fetcher not found: $NbaApiStep1" -ForegroundColor Red
        return $false
    }
    $outCsv = Join-Path $WnbaRunOutDir "step1_wnba_props.csv"
    $savedImp = [string]$env:PROPORACLE_CURL_IMPERSONATE
    $env:PROPORACLE_CURL_IMPERSONATE = "chrome120"
    try {
        $step1Args = "--league_id 3 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output `"$outCsv`" --date $Date"
        return (Run-Step "WNBA Step 1 - Fetch PrizePicks (NBA API fallback, chrome120)" $WNBADir $NbaApiStep1 $step1Args)
    } finally {
        if ($savedImp) { $env:PROPORACLE_CURL_IMPERSONATE = $savedImp }
        else { Remove-Item Env:PROPORACLE_CURL_IMPERSONATE -ErrorAction SilentlyContinue }
    }
}
$StartTime = Get-Date
$script:ProgressDone = 0
$script:ProgressTotal = if ($SkipFetch) { 9 } else { 10 }  # pipeline stages + optional fetch (no dated copy batch)

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

# Step 1 — PrizePicks: HTTP API (curl_cffi) when it works; CDP browser when DataDome blocks (Press & Hold).
# WNBA league_id=3 on board (NBA is 7). Do not close CDP Chrome between solve-challenge and fetch.
$step1Csv = Join-Path $WnbaRunOutDir "step1_wnba_props.csv"
if ($SkipFetch -and (Get-CsvDataRowCount -CsvPath $step1Csv) -eq 0) {
    Write-Host "  [WNBA step1] Existing step1 is empty — forcing fresh fetch" -ForegroundColor Yellow
    $SkipFetch = $false
}

if (-not $SkipFetch) {
    $cdpDefault = if ($Cdp) { $Cdp } else { "http://127.0.0.1:9222" }
    $cdpReachable = Test-CdpEndpoint -BaseUrl $cdpDefault
    $browserFirst = $UsePlaywright -or $Cdp -or $PreferBrowser -or $CdpWhenListening
    $useBrowserFirst = $browserFirst -and ($Cdp -or $UsePlaywright -or $cdpReachable)

    if ($useBrowserFirst) {
        if (-not $cdpReachable -and -not $UsePlaywright) {
            Write-Host "  [WNBA step1] WARN: CDP not reachable at $cdpDefault — launch Chrome:" -ForegroundColor Yellow
            Write-Host "    pwsh -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3" -ForegroundColor Cyan
        }
        if ($ok) {
            $ok = Invoke-WnbaStep1Browser -CdpUrl $(if ($Cdp) { $Cdp } elseif ($cdpReachable) { $cdpDefault } else { "" }) `
                -Label "WNBA Step 1 - Fetch PrizePicks (browser/CDP)"
        }
    } else {
        if ($ok) {
            $ok = Invoke-WnbaStep1Http -Label "WNBA Step 1 - Fetch PrizePicks (HTTP, chrome131)"
        }
        if (-not $ok) {
            Write-Host "  [WNBA step1] HTTP (chrome131) failed — retrying NBA API path (chrome120)..." -ForegroundColor Yellow
            $ok = Invoke-WnbaStep1HttpNbaFallback
        }
        if (-not $ok -and -not $HttpOnly -and -not $NoCdpFallback) {
            if ($cdpReachable) {
                Write-Host "  [WNBA step1] HTTP blocked — retrying via Chrome CDP ($cdpDefault)..." -ForegroundColor Yellow
                $ok = Invoke-WnbaStep1Browser -CdpUrl $cdpDefault -Label "WNBA Step 1 - Fetch PrizePicks (CDP fallback)"
            } else {
                Write-Host "  [WNBA step1] HTTP failed and CDP not running. DataDome bypass:" -ForegroundColor Red
                Write-Host "    1) pwsh -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3" -ForegroundColor Cyan
                Write-Host "    2) Complete Press & Hold until WNBA board loads" -ForegroundColor Cyan
                Write-Host "    3) pwsh -File scripts\run_wnba_pipeline.ps1 -Cdp $cdpDefault -Date $Date" -ForegroundColor Cyan
            }
        }
    }
} else {
    Write-Host "  --> [SkipFetch] Using existing $WnbaRunOutDir\step1_wnba_props.csv" -ForegroundColor DarkGray
    Write-Host "  [WNBA] If combined dropped WNBA rows, re-run step1 once (no SkipFetch) after board/game_date policy changes." -ForegroundColor DarkYellow
}

if ($Step1Only) {
    $n = Get-CsvDataRowCount -CsvPath $step1Csv
    if (-not $ok -or $n -eq 0) {
        Write-Host "  [WNBA] Step1Only failed (ok=$ok rows=$n)" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [WNBA] Step1Only complete ($n rows) -> $step1Csv" -ForegroundColor Green
    exit 0
}

if ($ok) { $ok = Run-Step "WNBA Step 2 - Attach Pick Types" $WNBADir ".\step2_attach_picktypes.py" `
    "--input `"$WnbaRunOutDir\step1_wnba_props.csv`" --output `"$WnbaRunOutDir\step2_wnba_picktypes.csv`"" }

if ($ok) { $ok = Run-Step "WNBA Step 3 - Attach Defense" $WNBADir ".\step3_attach_defense.py" `
    "--input `"$WnbaRunOutDir\step2_wnba_picktypes.csv`" --defense wnba_defense_summary.csv --output `"$WnbaRunOutDir\step3_wnba_defense.csv`"" }

$step4Attach = ""
if ($StatsFrom2025End) {
    $step4Attach = " --attach-stats-through 2025-10-20 --attach-stats-season 2025 --attach-stats-lookback-days 240"
    Write-Host "  [WNBA step4] StatsFrom2025End: rolling stats use only 2025 season through 2025-10-20 (overrides prior-season merge)." -ForegroundColor DarkCyan
}
$step4NoPrior = ""
if ($NoStatsFrom2025End -and -not $StatsFrom2025End) {
    $step4NoPrior = " --no-include-prior-season-stats"
    Write-Host "  [WNBA step4] NoStatsFrom2025End: rolling stats use current season cache only (--no-include-prior-season-stats)." -ForegroundColor DarkCyan
}
# Default step4 merges 2025+2026 cache rows + ~420d lookback for L5/L10 early in the new season (see step4 --no-include-prior-season-stats).
if (-not $StatsFrom2025End -and -not $NoStatsFrom2025End) {
    Write-Host "  [WNBA] L5/L10 use last games from current + prior season in ESPN cache (2025+2026 by default)." -ForegroundColor DarkCyan
}
if ($ok) { $ok = Run-Step "WNBA Step 4 - Player Stats (ESPN)" $WNBADir ".\step4_fetch_player_stats.py" `
    "--slate `"$WnbaRunOutDir\step3_wnba_defense.csv`" --out `"$WnbaRunOutDir\step4_wnba_stats.csv`" --season 2026 --date $Date --days 35 --cache wnba_espn_cache.csv --sleep 0.8 --retries 4 --timeout 30 --debug-misses wnba_no_espn_debug.csv$step4Attach$step4NoPrior" }

if ($ok) { $ok = Run-Step "WNBA Step 4b - Usage/Pace/Star Context" $WNBADir ".\scripts\step4b_attach_wnba_context.py" `
    "--input `"$WnbaRunOutDir\step4_wnba_stats.csv`" --output `"$WnbaRunOutDir\step4_wnba_stats.csv`" --season 2025" -TimeoutSeconds 600 }

if ($ok) { $ok = Run-Step "WNBA Step 5 - Line Hit Rates" $WNBADir ".\step5_add_line_hit_rates.py" `
    "--input `"$WnbaRunOutDir\step4_wnba_stats.csv`" --output `"$WnbaRunOutDir\step5_wnba_hitrates.csv`" --compute10" }

if ($ok) { $ok = Run-Step "WNBA Step 6 - Team Role Context" $WNBADir ".\step6_team_role_context.py" `
    "--input `"$WnbaRunOutDir\step5_wnba_hitrates.csv`" --output `"$WnbaRunOutDir\step6_wnba_context.csv`"" }

# Top/bottom-3 team leaders vs opponent defense (feeds step7 top3_def_context / top3_under_context)
if ($ok) {
    $Top3Script = Join-Path $WNBADir "scripts\analyze_top_players_vs_defense.py"
    if (Test-Path -LiteralPath $Top3Script) {
        Write-Host "  --> WNBA Top-3 vs defense analysis (step7 input)" -ForegroundColor Yellow
        Push-Location $Root
        try {
            & py -3.14 $Top3Script
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      top3-vs-defense WARN (exit $LASTEXITCODE) — continuing" -ForegroundColor Yellow
            } else {
                Write-Host "      OK" -ForegroundColor Green
            }
        } finally { Pop-Location }
    }
}

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

# Refresh top/bottom-3 vs defense with tonight's ranked slate overlay
if ($ok) {
    $Top3Script = Join-Path $WNBADir "scripts\analyze_top_players_vs_defense.py"
    $Step8Csv = Join-Path $WnbaRunOutDir "step8_wnba_direction.csv"
    if ((Test-Path -LiteralPath $Top3Script) -and (Test-Path -LiteralPath $Step8Csv)) {
        Write-Host "  --> WNBA Top/bottom-3 vs defense (slate overlay)" -ForegroundColor Yellow
        Push-Location $Root
        try {
            & py -3.14 $Top3Script --slate $Step8Csv
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      top3-vs-defense slate WARN (exit $LASTEXITCODE) — continuing" -ForegroundColor Yellow
            }
        } finally { Pop-Location }
    }
}

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

# Matchup edge JSON — dedicated WNBA builder (top/bottom-5, UNDER edges). Must run after slate publish.
if ($ok) {
    $MeScript = Join-Path $WNBADir "scripts\build_wnba_matchup_edge_json.py"
    $SlateJson = Join-Path $Root "ui_runner\templates\slate_sport_wnba.json"
    if (Test-Path -LiteralPath $MeScript) {
        Write-Host "  --> WNBA — Rebuild matchup edge JSON (dedicated builder, post-slate)" -ForegroundColor Yellow
        Push-Location $Root
        try {
            $meArgs = @($MeScript)
            if (Test-Path -LiteralPath $SlateJson) {
                $meArgs += @("--slate", $SlateJson)
            }
            & py -3.14 @meArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Host "      matchup-edge WARN (exit $LASTEXITCODE) — continuing" -ForegroundColor Yellow
            } else {
                Write-Host "      OK" -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
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
