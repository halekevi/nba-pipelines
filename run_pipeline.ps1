#requires -Version 7.2
# ============================================================
#  PROP PIPELINE  -  Master Run Script  [MULTI-SPORT]
#
#  Usage:
#    .\run_pipeline.ps1                        # All sports parallel + Combined
#    .\run_pipeline.ps1 -NBAOnly               # NBA only + Combined
#    .\run_pipeline.ps1 -CBBOnly               # CBB only + Combined
#    .\run_pipeline.ps1 -NHLOnly               # NHL only + Combined
#    .\run_pipeline.ps1 -MLBOnly               # MLB only + Combined
#    .\run_pipeline.ps1 -SoccerOnly            # Soccer only + Combined
#    .\run_pipeline.ps1 -TennisOnly           # Tennis (light pipeline) + Combined
#    .\run_pipeline.ps1 -WNBAOnly              # WNBA only (season-gated)
#    .\run_pipeline.ps1 -CombinedOnly          # Re-run combined + web tickets (multi-sport /tickets JSON)
#    .\run_pipeline.ps1 -CombinedOnly -WebEvOnly   # Stricter /tickets: positive-EV gate only (+ Tennis bypass)
#    .\run_pipeline.ps1 -SkipFetch             # Skip step1 fetch for whatever sport(s) run
#    .\run_pipeline.ps1 -NBAOnly -SkipFetch    # NBA steps 2-8 + Combined
#    .\run_pipeline.ps1 -NHLOnly -SkipFetch    # NHL steps 2-8 + Combined
#    .\run_pipeline.ps1 -SoccerOnly -SkipFetch # Soccer steps 2-8 + Combined
#    .\run_pipeline.ps1 -TennisOnly -SkipFetch # Tennis steps 2-8 + Combined (no step1 fetch)
#    .\run_pipeline.ps1 -RefreshCache          # Wipe + rebuild ESPN cache before NBA
#    .\run_pipeline.ps1 -CacheAgeDays 7        # Auto-wipe cache if older than N days
#    .\run_pipeline.ps1 -SkipDailyGrader       # Skip run_grader + grade HTML git push after combined
#    .\run_pipeline.ps1 -SkipAltBooks          # Skip Underdog + DraftKings fetch before combined (geo/403)
#
#  Combined always auto-includes every sport whose step8 output exists on disk.
#  No -Include flags needed -- just run any sport, combined picks it up.
#
#  Prefer .\run_pipeline.ps1 from repo root; this copy under scripts\ is equivalent.
#  After combined + git push, runs scripts\run_grader.ps1 for (pipeline date - 1 day)
#  and pushes slate_eval_*.html / ticket_eval_*.html for that slate date.
# ============================================================
param(
    [string]$Date       = "",
    # Prefer env ODDS_API_KEY; pass -OddsApiKey only for one-off overrides (never commit keys).
    [string]$OddsApiKey = "",
    [switch]$NBAOnly,
    [switch]$CBBOnly,
    [switch]$NHLOnly,
    [switch]$MLBOnly,
    [switch]$SoccerOnly,
    [switch]$TennisOnly,
    [switch]$WNBAOnly,
    [switch]$CombinedOnly,
    [switch]$SkipFetch,
    [switch]$RefreshCache,
    [switch]$ForceAll,
    [switch]$SkipDailyGrader,
    [switch]$RunPayoutEngine,
    [switch]$SkipAltBooks,
    [int]$CacheAgeDays = 7,
    # By default /tickets JSON includes MLB/NHL/Soccer slips (not only strict positive-EV + Tennis).
    # Pass -WebEvOnly to restore the stricter web JSON filter.
    [switch]$WebEvOnly
)

$ErrorActionPreference = "Continue"

if (-not $OddsApiKey) {
    $OddsApiKey = [string]$env:ODDS_API_KEY
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

$StartTime = Get-Date

# -- Paths --------------------------------------------------------------------
# Script may live at repo root or under scripts\; jobs also need a stable $Root for absolute step8 paths.
$Root = $PSScriptRoot
if ((Split-Path -Leaf $Root) -eq "scripts") {
    $Root = Split-Path -Parent $Root
}
$NBADir    = Join-Path $Root "NBA"
$CBBDir    = Join-Path $Root "CBB"
$NHLDir    = Join-Path $Root "NHL"
$MLBDir    = Join-Path $Root "MLB"
$SoccerDir = Join-Path $Root "Soccer"
$TennisDir = Join-Path $Root "Tennis"
$WNBADir   = Join-Path $Root "WNBA"
$NFLDir    = Join-Path $Root "NFL"
$OutDir    = Join-Path $Root "outputs\$Date"
$WebOutDir = Join-Path $Root "ui_runner\templates"

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

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
        [string]$Arguments = ""
    )
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        # Child Python inherits these; avoids UnicodeEncodeError on emoji logs (e.g. MLB step1) if the shell was cold-started without UTF-8.
        $env:PYTHONUTF8       = "1"
        $env:PYTHONIOENCODING = "utf-8"
        $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
        Write-Host "        CMD: $cmd" -ForegroundColor DarkGray
        $output = Invoke-Expression $cmd 2>&1
        $exit   = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) { Write-Host "      FAILED (exit $exit)" -ForegroundColor Red; return $false }
        Write-Host "      OK" -ForegroundColor Green; return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red; return $false
    } finally {
        Pop-Location
    }
}

function Invoke-MLBStep1Fetch {
    param(
        [string]$WorkDir,
        [string]$PipelineDate
    )
    Write-Host "  --> MLB Step 1 - Fetch PrizePicks (direct API, then Playwright if needed)" -ForegroundColor Yellow
    Push-Location $WorkDir
    try {
        $env:PYTHONUTF8       = "1"
        $env:PYTHONIOENCODING = "utf-8"
        $cmd1 = "py -3.14 -u `".\scripts\step1_fetch_prizepicks_mlb.py`" --date `"$PipelineDate`" --output step1_mlb_props.csv"
        Write-Host "        CMD: $cmd1" -ForegroundColor DarkGray
        $output = Invoke-Expression $cmd1 2>&1
        $exit   = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) {
            Write-Host "      MLB direct API failed (exit $exit); trying Playwright..." -ForegroundColor Yellow
            $cmd2 = "py -3.14 -u `".\scripts\step1_fetch_prizepicks_mlb.py`" --playwright --timeout 180 --date `"$PipelineDate`" --output step1_mlb_props.csv"
            Write-Host "        CMD: $cmd2" -ForegroundColor DarkGray
            $output = Invoke-Expression $cmd2 2>&1
            $exit   = $LASTEXITCODE
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

function Get-MLBStep1DateHealth {
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

function Clear-MLBGeneratedOutputs {
    param([string]$BaseDir)
    foreach ($p in @(
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

    $step1 = "step1_${tagLower}_props.csv"
    $step2 = "step2_${tagLower}_picktypes.csv"
    $step3 = "step3_${tagLower}_with_defense.csv"
    $step4 = "step4_${tagLower}_with_stats.csv"
    $step5 = "step5_${tagLower}_with_hit_rates.csv"
    $step6 = "step6_${tagLower}_with_team_role_context.csv"
    $step7 = "step7_${tagLower}_ranked_props.xlsx"
    $step8Csv = "step8_${tagLower}_direction.csv"
    $step8Xlsx = "step8_${tagLower}_direction_clean.xlsx"
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
                        Remove-Item (Join-Path $NBADir $stale) -Force -ErrorAction SilentlyContinue
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
    if ($ok) { $ok = Run-Step "${tagLower} Step 5 - Line Hit Rates"         $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input $step4 --output $step5" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 6 - Team Role Context"      $NBADir ".\scripts\step6_team_role_context.py"              "--input $step5 --output $step6" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 7 - Rank Props"             $NBADir ".\scripts\step7_rank_props.py"                     "--input $step6 --output $step7" }
    if ($ok) { $ok = Run-Step "${tagLower} Step 8 - Direction Context"      $NBADir (Join-Path $Root "NBA\scripts\step8_add_direction_context.py") "--input $step7 --sheet ALL --output $step8Csv --xlsx $step8Xlsx --date $Date" }

    if ($ok -and (Test-Path (Join-Path $NBADir $step8Xlsx))) {
        Copy-Item (Join-Path $NBADir $step8Xlsx) $datedOut -Force -ErrorAction SilentlyContinue
        Write-Host "  [$tagLower] Dated copy -> $datedOut" -ForegroundColor DarkGray
    }
    if ($ok) { Write-Host "  $tagLower complete." -ForegroundColor Green } else { Write-Host "  $tagLower FAILED." -ForegroundColor Red }
    return $ok
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
    param([string]$GradeDate)

    Write-Host ""
    Write-Host "[ GIT ] Pushing grade HTML for slate date $GradeDate ..." -ForegroundColor Cyan
    $candidates = @(
        "ui_runner/templates/slate_eval_$GradeDate.html",
        "ui_runner/templates/ticket_eval_$GradeDate.html",
        "ui_runner/templates/graded_props_$GradeDate.json"
    )
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
    if ($SkipAltBooks) {
        Write-Host "  [alt-books] Skipped (-SkipAltBooks)" -ForegroundColor DarkGray
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
    Write-Host ""
    $label = if ($Reason) { "[ COMBINED SLATE -- $Reason ]" } else { "[ COMBINED SLATE ]" }
    Write-Host $label -ForegroundColor Magenta
    Write-Host ""

    # Clean up any stale root-level combined_slate_tickets files from previous runs
    Get-ChildItem -Path $Root -Filter "combined_slate_tickets_*.xlsx" | Remove-Item -Force -ErrorAction SilentlyContinue

    $nbaFile    = "$NBADir\data\outputs\step8_all_direction_clean.xlsx"
    if (-not (Test-Path $nbaFile)) {
        $nbaAlt = Join-Path $NBADir "step8_all_direction_clean.xlsx"
        if (Test-Path $nbaAlt) {
            $nbaFile = $nbaAlt
            Write-Host "  [NBA] Using fallback step8: $nbaFile" -ForegroundColor DarkGray
        }
    }
    # CBB deactivated - season over (April 2026)
    $cbbFile    = "$CBBDir\step6_ranked_cbb.xlsx"
    $nhlFile    = "$NHLDir\outputs\step8_nhl_direction_clean.xlsx"
    $soccerFile = "$SoccerDir\outputs\step8_soccer_direction_clean.xlsx"
    $tennisFile = "$TennisDir\outputs\step8_tennis_direction_clean.xlsx"
    $mlbFile    = "$MLBDir\step8_mlb_direction_clean.xlsx"
    $nba1qFile  = "$NBADir\step8_nba1q_direction_clean.xlsx"
    $nba1hFile  = "$NBADir\step8_nba1h_direction_clean.xlsx"

    if (-not (Test-Path $nbaFile)) {
        Write-Host "  WARNING: NBA step8 not found -- combined will run with 0 NBA props (other sports only)" -ForegroundColor Yellow
    }

    Invoke-AltBookFetches

    $CombinedOut  = Join-Path $Root "combined_slate_tickets_$Date.xlsx"
    $CombinedArgs  = "--nba `"$nbaFile`""
    if (Test-Path $cbbFile) { Write-Host "  [CBB] present on disk but deactivated for combined build" -ForegroundColor DarkGray }

    if (Test-Path $nhlFile)    { $CombinedArgs += " --nhl `"$nhlFile`"";       Write-Host "  [+] NHL"    -ForegroundColor DarkGray }
    if (Test-Path $soccerFile) { $CombinedArgs += " --soccer `"$soccerFile`""; Write-Host "  [+] Soccer" -ForegroundColor DarkGray }
    if (Test-Path $tennisFile) { $CombinedArgs += " --tennis `"$tennisFile`""; Write-Host "  [+] Tennis" -ForegroundColor DarkGray }
    if (Test-Path $mlbFile)    { $CombinedArgs += " --mlb `"$mlbFile`"";       Write-Host "  [+] MLB"    -ForegroundColor DarkGray }
    if (Test-Path $nba1qFile)  { $CombinedArgs += " --nba1q `"$nba1qFile`"";   Write-Host "  [+] NBA1Q"  -ForegroundColor DarkGray }
    if (Test-Path $nba1hFile)  { $CombinedArgs += " --nba1h `"$nba1hFile`"";   Write-Host "  [+] NBA1H"  -ForegroundColor DarkGray }

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

    # Keep strict date checks for NBA-family slates so /tickets never shows yesterday as today.
    $CombinedArgs += " --date $Date --output `"$CombinedOut`" --tiers A,B,C,D --max-tickets 8 --ticket-gen-starts 48 --nba-structured-variants 3 --write-web --merge-web-latest --web-outdir `"$WebOutDir`""
    if (-not $WebEvOnly) {
        $CombinedArgs += " --no-web-ev-gate"
    }

    $okC = Run-Step "Combined Slate + Tickets" $Root ".\scripts\combined_slate_tickets.py" $CombinedArgs

    if ($okC) {
        Copy-Item $CombinedOut (Join-Path $OutDir "combined_slate_tickets_$Date.xlsx") -Force -ErrorAction SilentlyContinue
        Remove-Item $CombinedOut -Force -ErrorAction SilentlyContinue
        Write-Host "  Saved -> $(Join-Path $OutDir "combined_slate_tickets_$Date.xlsx")" -ForegroundColor Green
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
        Run-GitPush
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
    Write-Host "  Delegating to run_wnba_pipeline.ps1 ..." -ForegroundColor DarkGray
    & (Join-Path $Root "run_wnba_pipeline.ps1") -Date $Date
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"         "--output outputs\step1_nhl_props.csv" } } else { Write-Host "  [NHL] Skipping step1 fetch -- using existing outputs\step1_nhl_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input outputs\step1_nhl_props.csv --output outputs\step2_nhl_picktypes.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input outputs\step2_nhl_picktypes.csv --output outputs\step3_nhl_with_defense.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input outputs\step3_nhl_with_defense.csv --output outputs\step4_nhl_with_stats.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input outputs\step4_nhl_with_stats.csv --output outputs\step5_nhl_hit_rates.csv --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input outputs\step5_nhl_hit_rates.csv --output outputs\step6_nhl_role_context.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input outputs\step6_nhl_role_context.csv --output outputs\step7_nhl_ranked.xlsx" }
    if ($ok) { Invoke-PropOracleStep7b "NHL" }
    if ($ok) { $ok = Run-Step "NHL Step 8 - Direction Context"  $NHLDir (Join-Path $Root "NHL\scripts\step8_add_direction_context_nhl.py")  "--input outputs\step7_nhl_ranked.xlsx --output outputs\step8_nhl_direction_clean.xlsx" }
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
        Clear-MLBGeneratedOutputs -BaseDir $MLBDir
        if ($ok) { $ok = Invoke-MLBStep1Fetch -WorkDir $MLBDir -PipelineDate $Date }
        if ($ok) {
            $mlbStep1Health = Get-MLBStep1DateHealth -CsvPath (Join-Path $MLBDir "step1_mlb_props.csv") -TargetDate $Date
            if (-not $mlbStep1Health.ok) {
                Write-Host "  [MLB] Step1 date health failed ($($mlbStep1Health.reason)); clearing MLB outputs to avoid stale carry-over." -ForegroundColor Yellow
                Clear-MLBGeneratedOutputs -BaseDir $MLBDir
                $ok = $false
            }
        }
    } else {
        Write-Host "  [MLB] Skipping step1 fetch -- using existing step1_mlb_props.csv" -ForegroundColor DarkGray
    }
    if ($ok) { $ok = Run-Step "MLB Step 2 - Attach Pick Types"  $MLBDir ".\scripts\step2_attach_picktypes_mlb.py"       "--input step1_mlb_props.csv --output step2_mlb_picktypes.csv --id_lookup_timeout_s 6 --id_lookup_retries 2 --id_lookup_budget_s 180" }
    if ($ok) { $ok = Run-Step "MLB Step 3 - Attach Defense"     $MLBDir ".\scripts\step3_attach_defense_mlb.py"         "--input step2_mlb_picktypes.csv --defense mlb_defense_summary.csv --output step3_mlb_with_defense.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 4 - Player Stats"       $MLBDir ".\scripts\step4_attach_player_stats_mlb.py"    "--input step3_mlb_with_defense.csv --cache mlb_stats_cache.csv --output step4_mlb_with_stats.csv --season 2025" }
    if ($ok) { $ok = Run-Step "MLB Step 5 - Line Hit Rates"     $MLBDir ".\scripts\step5_add_line_hit_rates_mlb.py"     "--input step4_mlb_with_stats.csv --output step5_mlb_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 6 - Team Role Context"  $MLBDir ".\scripts\step6_team_role_context_mlb.py"      "--input step5_mlb_hit_rates.csv --output step6_mlb_role_context.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 7 - Rank Props"         $MLBDir ".\scripts\step7_rank_props_mlb.py"             "--input step6_mlb_role_context.csv --output step7_mlb_ranked.xlsx" }
    if ($ok) { Invoke-PropOracleStep7b "MLB" }
    if ($ok) { $ok = Run-Step "MLB Step 8 - Direction Context"  $MLBDir (Join-Path $Root "MLB\scripts\step8_add_direction_context_mlb.py")  "--input step7_mlb_ranked.xlsx --output step8_mlb_direction.csv --xlsx step8_mlb_direction_clean.xlsx --date $Date" }
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output outputs\step1_soccer_props.csv --date $Date" } } else { Write-Host "  [Soccer] Skipping step1 fetch -- using existing outputs\step1_soccer_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input outputs\step1_soccer_props.csv --output outputs\step2_soccer_picktypes.csv" }
    if ($ok) { $ok = Run-Step "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input outputs\step2_soccer_picktypes.csv --defense cache\soccer_defense_summary.csv --output outputs\step3_soccer_with_defense.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input outputs\step3_soccer_with_defense.csv --output outputs\step4_soccer_with_stats.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input outputs\step4_soccer_with_stats.csv --output outputs\step5_soccer_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input outputs\step5_soccer_hit_rates.csv --output outputs\step6_soccer_role_context.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input outputs\step6_soccer_role_context.csv --output outputs\step7_soccer_ranked.xlsx" }
    if ($ok) { Invoke-PropOracleStep7b "Soccer" }
    if ($ok) { $ok = Run-Step "Soccer Step 8 - Direction Context"  $SoccerDir (Join-Path $Root "Soccer\scripts\step8_add_direction_context_soccer.py")  "--input outputs\step7_soccer_ranked.xlsx --sheet ALL --output outputs\step8_soccer_direction.csv --xlsx outputs\step8_soccer_direction_clean.xlsx --date $Date" }
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
    $ok = $true
    if (-not $SkipFetch) {
        if ($ok) { $ok = Run-Step "Tennis Step 1 - Fetch PrizePicks" $TennisDir ".\scripts\step1_fetch_prizepicks_tennis.py" "--output outputs\step1_tennis_props.csv" }
    } else {
        Write-Host "  [Tennis] Skipping step1 fetch -- using existing outputs\step1_tennis_props.csv" -ForegroundColor DarkGray
    }
    if ($ok) { $ok = Run-Step "Tennis Step 2 - Attach Pick Types" $TennisDir ".\scripts\step2_attach_picktypes_tennis.py" "--input outputs\step1_tennis_props.csv --output outputs\step2_tennis_picktypes.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 3 - Defense Stub" $TennisDir ".\scripts\step3_defense_rankings_tennis.py" "--input outputs\step2_tennis_picktypes.csv --output outputs\step3_tennis_with_defense.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 4 - Player Stats + History" $TennisDir ".\scripts\step4_attach_player_stats_tennis.py" "--input outputs\step3_tennis_with_defense.csv --output outputs\step4_tennis_with_stats.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 5 - Hit Rates" $TennisDir ".\scripts\step5_compute_hitrates_tennis.py" "--input outputs\step4_tennis_with_stats.csv --output outputs\step5_tennis_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step "Tennis Step 6 - Context" $TennisDir ".\scripts\step6_add_context_tennis.py" "--input outputs\step5_tennis_hit_rates.csv --output outputs\step6_tennis_role_context.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 7 - Rank Props" $TennisDir ".\scripts\step7_rank_props_tennis.py" "--input outputs\step6_tennis_role_context.csv --output outputs\step7_tennis_ranked.xlsx" }
    if ($ok) { Invoke-PropOracleStep7b "Tennis" }
    if ($ok) { $ok = Run-Step "Tennis Step 8 - Direction Context" $TennisDir (Join-Path $Root "Tennis\scripts\step8_add_direction_context_tennis.py") "--input outputs\step7_tennis_ranked.xlsx --sheet ALL --output outputs\step8_tennis_direction.csv --xlsx outputs\step8_tennis_direction_clean.xlsx --date $Date" }
    Write-Host ""
    if ($ok) { Write-Host "  Tennis complete." -ForegroundColor Green } else { Write-Host "  Tennis FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after Tennis" }
    Print-Done
    exit
}

# =============================================================================
#  CBB ONLY
# =============================================================================
if ($CBBOnly) {
    Write-Host "[ CBB PIPELINE ]" -ForegroundColor Magenta
    Write-Host "  CBB deactivated - season over (April 2026)." -ForegroundColor Yellow
    exit 0
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "CBB Step 1 - Fetch PrizePicks"      $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py"      "--out step1_cbb.csv" } } else { Write-Host "  [CBB] Skipping step1 fetch -- using existing step1_cbb.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input step1_cbb.csv --output step2_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input step2_cbb.csv --defense data\reference\cbb_def_rankings.csv --output step3b_with_def_rankings_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input step3b_with_def_rankings_cbb.csv --output step3_cbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input step3_cbb.csv --output step5b_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input step5b_cbb.csv --output step6_ranked_cbb.xlsx" }
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NBA Step 1 - Fetch PrizePicks"    $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output data\outputs\step1_pp_props_today.csv --date $Date" } } else { Write-Host "  [NBA] Skipping step1 fetch -- using existing data\outputs\step1_pp_props_today.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input data\outputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input data\outputs\step2_with_picktypes.csv --defense data\cache\defense_team_summary.csv --output data\outputs\step3_with_defense.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate data\outputs\step3_with_defense.csv --out data\outputs\step4_with_stats.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input data\outputs\step4_with_stats.csv --output data\outputs\step5_with_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input data\outputs\step5_with_hit_rates.csv --output data\outputs\step6_with_team_role_context.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input data\outputs\step6_with_team_role_context.csv --output data\outputs\step6a_with_opp_stats.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input data\outputs\step6a_with_opp_stats.csv --output data\outputs\step6b_with_game_context.csv --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input data\outputs\step6b_with_game_context.csv --output data\outputs\step6c_with_schedule_flags.csv --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input data\outputs\step6c_with_schedule_flags.csv --output data\outputs\step6d_with_h2h.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input data\outputs\step6d_with_h2h.csv --output data\outputs\step7_ranked_props.xlsx" }
    if ($ok) { Invoke-PropOracleStep7b "NBA" }
    if ($ok) { $ok = Run-Step "NBA Step 8 - Direction Context"       $NBADir (Join-Path $Root "NBA\scripts\step8_add_direction_context.py")         "--input data\outputs\step7_ranked_props.xlsx --sheet ALL --output data\outputs\step8_all_direction.csv --date $Date" }
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
#  FULL PARALLEL RUN  (NBA + NHL + Soccer + MLB; CBB deactivated)
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

Write-Host "[ PARALLEL PIPELINE: NBA + NHL + Soccer + Tennis + MLB ]" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Starting all pipelines simultaneously..." -ForegroundColor Cyan
Write-Host ""

# -- NBA Job ------------------------------------------------------------------
$NBAJob = Start-Job -ScriptBlock {
    param($NBADir, $Date, $OddsApiKey, $SkipFetch, $RepoRoot)
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NBA Step 1 - Fetch PrizePicks"    $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --replace --output data\outputs\step1_pp_props_today.csv --date $Date" } } else { Write-Output "[NBA] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input data\outputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input data\outputs\step2_with_picktypes.csv --defense data\cache\defense_team_summary.csv --output data\outputs\step3_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate data\outputs\step3_with_defense.csv --out data\outputs\step4_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input data\outputs\step4_with_stats.csv --output data\outputs\step5_with_hit_rates.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input data\outputs\step5_with_hit_rates.csv --output data\outputs\step6_with_team_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input data\outputs\step6_with_team_role_context.csv --output data\outputs\step6a_with_opp_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input data\outputs\step6a_with_opp_stats.csv --output data\outputs\step6b_with_game_context.csv --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input data\outputs\step6b_with_game_context.csv --output data\outputs\step6c_with_schedule_flags.csv --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input data\outputs\step6c_with_schedule_flags.csv --output data\outputs\step6d_with_h2h.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input data\outputs\step6d_with_h2h.csv --output data\outputs\step7_ranked_props.xlsx" }
    if ($ok) { Invoke-Step7b-Job "NBA" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "NBA Step 8 - Direction Context"       $NBADir (Join-Path $RepoRoot "NBA\scripts\step8_add_direction_context.py")         "--input data\outputs\step7_ranked_props.xlsx --sheet ALL --output data\outputs\step8_all_direction.csv --date $Date" }
    return $ok
} -ArgumentList $NBADir, $Date, $OddsApiKey, $SkipFetch, $Root

# -- CBB Job ------------------------------------------------------------------
# CBB deactivated - season over (April 2026)
$CBBJob = $null

# -- NHL Job ------------------------------------------------------------------
$NHLJob = Start-Job -ScriptBlock {
    param($NHLDir, $SkipFetch, $RepoRoot)
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"        "--output outputs\step1_nhl_props.csv" } } else { Write-Output "[NHL] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input outputs\step1_nhl_props.csv --output outputs\step2_nhl_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input outputs\step2_nhl_picktypes.csv --output outputs\step3_nhl_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input outputs\step3_nhl_with_defense.csv --output outputs\step4_nhl_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input outputs\step4_nhl_with_stats.csv --output outputs\step5_nhl_hit_rates.csv --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input outputs\step5_nhl_hit_rates.csv --output outputs\step6_nhl_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input outputs\step6_nhl_role_context.csv --output outputs\step7_nhl_ranked.xlsx" }
    if ($ok) { Invoke-Step7b-Job "NHL" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "NHL Step 8 - Direction Context"  $NHLDir (Join-Path $RepoRoot "NHL\scripts\step8_add_direction_context_nhl.py")  "--input outputs\step7_nhl_ranked.xlsx --output outputs\step8_nhl_direction_clean.xlsx" }
    return $ok
} -ArgumentList $NHLDir, $SkipFetch, $Root

# -- Soccer Job ---------------------------------------------------------------
$SoccerJob = Start-Job -ScriptBlock {
    param($SoccerDir, $Date, $SkipFetch, $RepoRoot)
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output outputs\step1_soccer_props.csv --date $Date" } } else { Write-Output "[Soccer] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input outputs\step1_soccer_props.csv --output outputs\step2_soccer_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input outputs\step2_soccer_picktypes.csv --defense cache\soccer_defense_summary.csv --output outputs\step3_soccer_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input outputs\step3_soccer_with_defense.csv --output outputs\step4_soccer_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input outputs\step4_soccer_with_stats.csv --output outputs\step5_soccer_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input outputs\step5_soccer_hit_rates.csv --output outputs\step6_soccer_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input outputs\step6_soccer_role_context.csv --output outputs\step7_soccer_ranked.xlsx" }
    if ($ok) { Invoke-Step7b-Job "Soccer" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 8 - Direction Context"  $SoccerDir (Join-Path $RepoRoot "Soccer\scripts\step8_add_direction_context_soccer.py")  "--input outputs\step7_soccer_ranked.xlsx --sheet ALL --output outputs\step8_soccer_direction.csv --xlsx outputs\step8_soccer_direction_clean.xlsx --date $Date" }
    return $ok
} -ArgumentList $SoccerDir, $Date, $SkipFetch, $Root

# -- Tennis Job ---------------------------------------------------------------
$TennisJob = Start-Job -ScriptBlock {
    param($TennisDir, $Date, $SkipFetch, $RepoRoot)
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
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "Tennis Step 1 - Fetch PrizePicks" $TennisDir ".\scripts\step1_fetch_prizepicks_tennis.py" "--output outputs\step1_tennis_props.csv" } } else { Write-Output "[Tennis] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 2 - Attach Pick Types" $TennisDir ".\scripts\step2_attach_picktypes_tennis.py" "--input outputs\step1_tennis_props.csv --output outputs\step2_tennis_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 3 - Defense Stub" $TennisDir ".\scripts\step3_defense_rankings_tennis.py" "--input outputs\step2_tennis_picktypes.csv --output outputs\step3_tennis_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 4 - Player Stats + History" $TennisDir ".\scripts\step4_attach_player_stats_tennis.py" "--input outputs\step3_tennis_with_defense.csv --output outputs\step4_tennis_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 5 - Hit Rates" $TennisDir ".\scripts\step5_compute_hitrates_tennis.py" "--input outputs\step4_tennis_with_stats.csv --output outputs\step5_tennis_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 6 - Context" $TennisDir ".\scripts\step6_add_context_tennis.py" "--input outputs\step5_tennis_hit_rates.csv --output outputs\step6_tennis_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 7 - Rank Props" $TennisDir ".\scripts\step7_rank_props_tennis.py" "--input outputs\step6_tennis_role_context.csv --output outputs\step7_tennis_ranked.xlsx" }
    if ($ok) { Invoke-Step7b-Job "Tennis" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 8 - Direction Context" $TennisDir (Join-Path $RepoRoot "Tennis\scripts\step8_add_direction_context_tennis.py") "--input outputs\step7_tennis_ranked.xlsx --sheet ALL --output outputs\step8_tennis_direction.csv --xlsx outputs\step8_tennis_direction_clean.xlsx --date $Date" }
    return $ok
} -ArgumentList $TennisDir, $Date, $SkipFetch, $Root

# -- MLB Job ------------------------------------------------------------------
# MLB activated April 2026
$MLBJob = Start-Job -ScriptBlock {
    param($MLBDir, $Date, $SkipFetch, $RepoRoot)
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
        param([string]$Dir, [string]$PipelineDate)
        Write-Output "[MLB] --> MLB Step 1 - Fetch PrizePicks (direct API, then Playwright if needed)"
        Push-Location $Dir
        try {
            $cmd1 = "py -3.14 -u `".\scripts\step1_fetch_prizepicks_mlb.py`" --date `"$PipelineDate`" --output step1_mlb_props.csv"
            Write-Output "        CMD: $cmd1"
            $output = Invoke-Expression $cmd1 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) {
                Write-Output "[MLB] Direct API failed (exit $exit); trying Playwright"
                $cmd2 = "py -3.14 -u `".\scripts\step1_fetch_prizepicks_mlb.py`" --playwright --timeout 180 --date `"$PipelineDate`" --output step1_mlb_props.csv"
                Write-Output "        CMD: $cmd2"
                $output = Invoke-Expression $cmd2 2>&1; $exit = $LASTEXITCODE
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
        }
        return @{ ok = ($match.Count -gt 0); reason = (if ($match.Count -gt 0) { "ok" } else { "date_mismatch" }) }
    }
    function Clear-MLBGeneratedOutputs-Job {
        param([string]$BaseDir)
        foreach ($p in @(
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
        Clear-MLBGeneratedOutputs-Job -BaseDir $MLBDir
        if ($ok) { $ok = Invoke-MLBStep1Fetch-Job -Dir $MLBDir -PipelineDate $Date }
        if ($ok) {
            $health = Get-MLBStep1DateHealth-Job -CsvPath (Join-Path $MLBDir "step1_mlb_props.csv") -TargetDate $Date
            if (-not $health.ok) {
                Write-Output "[MLB] Step1 date health failed ($($health.reason)); clearing MLB outputs to avoid stale carry-over."
                Clear-MLBGeneratedOutputs-Job -BaseDir $MLBDir
                $ok = $false
            }
        }
    } else {
        Write-Output "[MLB] Skipping step1 fetch"
    }
    if ($ok) { $ok = Run-Step-Job "MLB Step 2 - Attach Pick Types"  $MLBDir ".\scripts\step2_attach_picktypes_mlb.py"       "--input step1_mlb_props.csv --output step2_mlb_picktypes.csv --id_lookup_timeout_s 6 --id_lookup_retries 2 --id_lookup_budget_s 180" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 3 - Attach Defense"     $MLBDir ".\scripts\step3_attach_defense_mlb.py"         "--input step2_mlb_picktypes.csv --defense mlb_defense_summary.csv --output step3_mlb_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 4 - Player Stats"       $MLBDir ".\scripts\step4_attach_player_stats_mlb.py"    "--input step3_mlb_with_defense.csv --cache mlb_stats_cache.csv --output step4_mlb_with_stats.csv --season 2025" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 5 - Line Hit Rates"     $MLBDir ".\scripts\step5_add_line_hit_rates_mlb.py"     "--input step4_mlb_with_stats.csv --output step5_mlb_hit_rates.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 6 - Team Role Context"  $MLBDir ".\scripts\step6_team_role_context_mlb.py"      "--input step5_mlb_hit_rates.csv --output step6_mlb_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 7 - Rank Props"         $MLBDir ".\scripts\step7_rank_props_mlb.py"             "--input step6_mlb_role_context.csv --output step7_mlb_ranked.xlsx" }
    if ($ok) { Invoke-Step7b-Job "MLB" $RepoRoot }
    if ($ok) { $ok = Run-Step-Job "MLB Step 8 - Direction Context"  $MLBDir (Join-Path $RepoRoot "MLB\scripts\step8_add_direction_context_mlb.py")  "--input step7_mlb_ranked.xlsx --output step8_mlb_direction.csv --xlsx step8_mlb_direction_clean.xlsx --date $Date" }
    return $ok
} -ArgumentList $MLBDir, $Date, $SkipFetch, $Root

# -- NFL Job (INACTIVE until Sept 2026 regular season) -------------------------
# NFL — activate September 2026 for regular season
# Uncomment when step6 historical data is populated and step8 exists.
# $NFLJob = Start-Job -ScriptBlock {
#     param($NFLDir, $Date, $SkipFetch)
#     $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
#     $env:NFL_PIPELINE_ACTIVE = "1"
#     function Run-Step-Job {
#         param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
#         Write-Output "[NFL] --> $Label"
#         Push-Location $Dir
#         try {
#             $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
#             Write-Output "        CMD: $cmd"
#             $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
#             foreach ($line in $output) { Write-Output "        $line" }
#             if ($exit -ne 0) { Write-Output "[NFL] FAILED: $Label (exit $exit)"; return $false }
#             Write-Output "[NFL] OK: $Label"; return $true
#         } catch { Write-Output "[NFL] EXCEPTION: $_"; return $false
#         } finally { Pop-Location }
#     }
#     $ok = $true
#     if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NFL Step 1 - Fetch PrizePicks" $NFLDir ".\scripts\step1_fetch_prizepicks_nfl.py" "--output data\outputs\step1_pp_props_today.csv --date $Date" } } else { Write-Output "[NFL] Skipping step1 fetch" }
#     if ($ok) { $ok = Run-Step-Job "NFL Step 2 - Clean Props" $NFLDir ".\scripts\step2_clean_props.py" "" }
#     if ($ok) { $ok = Run-Step-Job "NFL Step 4 - Defense Rankings" $NFLDir ".\scripts\step4_defense_rankings.py" "" }
#     if ($ok) { $ok = Run-Step-Job "NFL Step 6 - Hit Rates (skeleton)" $NFLDir ".\scripts\step6_historical_hit_rates.py" "" }
#     return $ok
# } -ArgumentList $NFLDir, $Date, $SkipFetch

# -- Wait + stream output -----------------------------------------------------
$allJobs = @($NBAJob, $NHLJob, $SoccerJob, $TennisJob, $MLBJob) | Where-Object { $_ -ne $null }

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
$NBASuccess    = Test-Path (Join-Path $NBADir    "data\outputs\step8_all_direction_clean.xlsx")
$CBBSuccess    = $true
$NHLSuccess    = Test-Path (Join-Path $NHLDir    "outputs\step8_nhl_direction_clean.xlsx")
$SoccerSuccess = Test-Path (Join-Path $SoccerDir "outputs\step8_soccer_direction_clean.xlsx")
$MLBSuccess    = Test-Path (Join-Path $MLBDir    "step8_mlb_direction_clean.xlsx")
$mlbStep1Health = Get-MLBStep1DateHealth -CsvPath (Join-Path $MLBDir "step1_mlb_props.csv") -TargetDate $Date
if (-not $mlbStep1Health.ok) {
    Write-Host "  [MLB] stale/invalid step1 for $Date ($($mlbStep1Health.reason)); clearing MLB outputs from this run." -ForegroundColor Yellow
    Clear-MLBGeneratedOutputs -BaseDir $MLBDir
    $MLBSuccess = $false
} else {
    $MLBSuccess = $MLBSuccess -and $true
}

# NBA period sub-slates are required by daily checks and combined defaults.
$NBA1HSuccess  = $false
$NBA1QSuccess  = $false
if ($NBASuccess) {
    $NBA1HSuccess = Run-NBAPeriodPipeline -Tag "nba1h" -LeagueId "84"  -SkipFetchStep:$SkipFetch
    $NBA1QSuccess = Run-NBAPeriodPipeline -Tag "nba1q" -LeagueId "192" -SkipFetchStep:$SkipFetch
}
$NBASuccess = $NBASuccess -and $NBA1HSuccess -and $NBA1QSuccess

Remove-Job $allJobs -Force -ErrorAction SilentlyContinue
if ($NBASuccess) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }

Write-Host ""
@(
    @{ Name="NBA";    Ok=$NBASuccess },
    @{ Name="NHL";    Ok=$NHLSuccess },
    @{ Name="Soccer"; Ok=$SoccerSuccess },
    @{ Name="MLB";    Ok=$MLBSuccess }
) | ForEach-Object {
    if ($_.Ok) { Write-Host "  $($_.Name) complete." -ForegroundColor Green }
    else        { Write-Host "  $($_.Name) FAILED."  -ForegroundColor Red   }
}

Run-Combined "full parallel run"
Print-Done
