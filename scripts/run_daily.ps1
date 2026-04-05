#requires -Version 7.2
<#
.SYNOPSIS
  Daily PropOracle run: grade yesterday, archive dated outputs, run today's full pipeline, combined slate, git push.

.NOTES
  Order: (A1) Refresh historical game logs → (A) Grader for yesterday → (A1b) build_ticket_eval for yesterday → (A1c) optional CLV Excel columns → (A2) consistency
         → (B) Archive outputs\<yesterday>\ step8 copies → (C0) fetch game lines → (C0b) rolling NBA 1Q/2Q DB sync
         → (C) run_pipeline for today → (D) combined_slate → (E) git commit/push.
         Use -SkipFetch to skip A1 and C0b. -SkipGameLines skips C0. -SkipPeriodHistorySync skips C0b only.
         -WeeklyAnalysis runs synthetic + full consistency rebuild after analyze_grader.
         -MonthlyRetrain after STEP E runs all four prop ML trainers + full consistency rebuild (logs OK/FAILED, continues on failure).
  $Root = parent of scripts\ (repo root).
#>
param(
    [switch]$SkipGrader,
    [switch]$SkipPipeline,
    [switch]$SkipPush,
    [switch]$SkipConsistency,
    [switch]$SkipFetch,
    [switch]$SkipGameLines,
    [switch]$WeeklyAnalysis,
    [switch]$MonthlyRetrain,
    [string]$Date = "",
    [string]$GradeDate = "",
    [string]$OddsApiKey = "",
    [switch]$ForceAll,
    [switch]$AllowMissingSlates,
    [switch]$SkipPeriodHistorySync,
    [int]$PeriodHistoryLookbackDays = 10,
    [int]$A1TimeoutMinutes = 30
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent

# Ensure local cache folder exists
# (excluded from OneDrive, must be created locally)
$CacheDir = Join-Path $Root "data\cache"
if (!(Test-Path $CacheDir)) {
    New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    Write-Host "Created local cache directory: $CacheDir" `
      -ForegroundColor DarkGray
}

$script:DailyStart = Get-Date
$script:PipelineFailed = $false
$script:WeeklyAnalysisReport = ""
function Get-TimeStamp { return Get-Date -Format "HH:mm:ss" }

$Today = if ($Date.Trim()) { $Date.Trim() } else { (Get-Date).ToString("yyyy-MM-dd") }
$Yesterday = if ($GradeDate.Trim()) { $GradeDate.Trim() } else { (Get-Date).AddDays(-1).ToString("yyyy-MM-dd") }

$LogsDir = Join-Path $Root "logs"
if (!(Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$LogFile = Join-Path $LogsDir "run_daily_$Today.log"

function Write-Log([string]$Message) {
    $line = "[$(Get-TimeStamp)] $Message"
    $line | Tee-Object -FilePath $LogFile -Append
}

function Get-MissingTodaySlateOutputs([string]$RunDate) {
    $outDir = Join-Path $Root "outputs\$RunDate"
    $required = @(
        "step8_nba_direction_clean_$RunDate.xlsx",
        "step8_nba1h_direction_clean_$RunDate.xlsx",
        "step8_nba1q_direction_clean_$RunDate.xlsx",
        "step6_ranked_cbb_$RunDate.xlsx",
        "step6_ranked_wcbb_$RunDate.xlsx",
        "step8_nhl_direction_clean_$RunDate.xlsx",
        "step8_soccer_direction_clean_$RunDate.xlsx",
        "step8_mlb_direction_clean_$RunDate.xlsx"
    )
    $missing = @()
    foreach ($name in $required) {
        $p = Join-Path $outDir $name
        if (-not (Test-Path $p)) { $missing += $name }
    }
    return $missing
}

# Python / UTF-8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Log "======== Daily run start (Today=$Today, Yesterday=$Yesterday) ========"

# =============================================================================
# STEP A1 — Refresh current season game logs (historical actuals)
# =============================================================================
if (-not $SkipFetch) {
    Write-Log "STEP A1 - Historical actuals refresh: START"
    $fetchScript = Join-Path $Root "scripts\fetch_historical_actuals.py"
    # NOTE: historical_actuals.db can be locked by OneDrive sync since this repo lives under OneDrive.
    # scripts\fetch_historical_actuals.py currently does NOT accept a --db override, so we can't redirect
    # the DB path from here without changing that script. If you hit "database is locked", pause OneDrive
    # or move the repo / DB to a non-synced location.
    Push-Location $Root
    try {
        # Run in a child process so daily cannot hang forever in A1.
        $a1Proc = Start-Process -FilePath "py" `
            -ArgumentList @("-3.14", "-u", $fetchScript, "--refresh-current") `
            -NoNewWindow -PassThru

        $waitSec = [Math]::Max(60, $A1TimeoutMinutes * 60)
        $a1Finished = $a1Proc.WaitForExit($waitSec * 1000)
        if (-not $a1Finished) {
            Write-Warning "fetch_historical_actuals.py exceeded timeout (${A1TimeoutMinutes}m) — continuing"
            Write-Log "STEP A1 - Historical actuals refresh: WARN (timeout ${A1TimeoutMinutes}m)"
            try {
                Stop-Process -Id $a1Proc.Id -Force -ErrorAction SilentlyContinue
            }
            catch { }
        }
        else {
            $fe = $a1Proc.ExitCode
            if ($fe -ne 0) {
                Write-Warning "fetch_historical_actuals.py exited $fe — continuing (see logs\fetch_errors.log)"
                Write-Log "STEP A1 - Historical actuals refresh: WARN (exit $fe)"
            }
            else {
                Write-Log "STEP A1 - Historical actuals refresh: OK"
            }
        }
    }
    catch {
        Write-Warning "fetch_historical_actuals failed — continuing"
        Write-Log "STEP A1 - Historical actuals refresh: FAILED (exception: $($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Log "STEP A1 - Historical actuals refresh: SKIPPED (-SkipFetch)"
}

# --- Odds API key: explicit param > env ---
$EffectiveOddsKey = $OddsApiKey.Trim()
if (-not $EffectiveOddsKey -and $env:ODDS_API_KEY) {
    $EffectiveOddsKey = $env:ODDS_API_KEY.Trim()
}

# =============================================================================
# STEP A — Grader for yesterday
# =============================================================================
$yesterdayCombined = Join-Path $Root "outputs\$Yesterday\combined_slate_tickets_$Yesterday.xlsx"
$yesterdayTixGraded = Join-Path $Root "outputs\$Yesterday\combined_tickets_graded_$Yesterday.xlsx"
if (-not $SkipGrader) {
    $gradedExpected = @(
        (Join-Path $Root "outputs\$Yesterday\graded_nba_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_cbb_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_nhl_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_soccer_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_mlb_$Yesterday.xlsx")
    )
    $missingGraded = @($gradedExpected | Where-Object { -not (Test-Path $_) })
    # If we have a ticket slate but never ran combined_ticket_grader, do not skip — otherwise legs stay UNGRADED in ticket_eval HTML.
    $needCombinedTicketWorkbook = (Test-Path $yesterdayCombined) -and -not (Test-Path $yesterdayTixGraded)
    if ($missingGraded.Count -eq 0 -and -not $needCombinedTicketWorkbook) {
        Write-Host "Grader outputs already present for $Yesterday — skipping" -ForegroundColor DarkYellow
        Write-Log "STEP A - Grader ($Yesterday): SKIPPED (all graded outputs present; combined ticket workbook OK)"
    }
    else {
        if ($needCombinedTicketWorkbook -and $missingGraded.Count -eq 0) {
            Write-Host "Re-running grader for $Yesterday: combined ticket graded workbook missing" -ForegroundColor DarkYellow
            Write-Log "STEP A - Grader ($Yesterday): START (combined_tickets_graded missing)"
        }
        else {
            Write-Host "Grader rerun for $Yesterday (missing: $($missingGraded.Count))" -ForegroundColor DarkYellow
            foreach ($m in $missingGraded) {
                Write-Host "  missing -> $m" -ForegroundColor DarkYellow
            }
            Write-Log "STEP A - Grader ($Yesterday): START"
        }
        $graderScript = Join-Path $Root "scripts\run_grader.ps1"
        try {
            & pwsh -NoProfile -File $graderScript -Date $Yesterday
            $graderExit = $LASTEXITCODE
            if ($graderExit -ne 0) {
                Write-Warning "Grader failed for $Yesterday — check logs (exit $graderExit)"
                Write-Log "STEP A - Grader ($Yesterday): FAILED (exit $graderExit)"
            }
            else {
                Write-Log "STEP A - Grader ($Yesterday): OK"
            }
        }
        catch {
            Write-Warning "Grader failed for $Yesterday — check logs"
            Write-Log "STEP A - Grader ($Yesterday): FAILED (exception: $($_.Exception.Message))"
        }
    }
}
else {
    Write-Log "STEP A - Grader ($Yesterday): SKIPPED (-SkipGrader)"
}

# =============================================================================
# STEP A1b — Ticket eval HTML for yesterday (always when slate exists)
# Grades are merged from outputs/<Yesterday>/graded_*.xlsx in build_ticket_eval.py.
# Step D only rebuilds ticket_eval for $Today, so without this pass yesterday's
# ticket_eval_*.html can stay all-UNGRADED if STEP A skipped run_grader or the
# eval step failed inside it.
# =============================================================================
$buildTicketEvalScript = Join-Path $Root "scripts\build_ticket_eval.py"
if (Test-Path $yesterdayCombined) {
    if (Test-Path $buildTicketEvalScript) {
        Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): START"
        Push-Location $Root
        try {
            & py -3.14 -X utf8 $buildTicketEvalScript --date $Yesterday
            $be = $LASTEXITCODE
            if ($be -ne 0) {
                Write-Warning "build_ticket_eval.py ($Yesterday) exited $be — yesterday's Grades tickets tab may be stale"
                Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): WARN (exit $be)"
            }
            else {
                Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): OK"
            }
        }
        catch {
            Write-Warning "build_ticket_eval.py ($Yesterday) threw: $_"
            Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): FAILED (exception: $($_.Exception.Message))"
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): SKIP (build_ticket_eval.py not found)"
    }
}
else {
    Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): SKIP (no outputs\$Yesterday\combined_slate_tickets_$Yesterday.xlsx)"
}

# =============================================================================
# STEP A1c — Add CLV columns to graded workbooks (when odds columns exist)
# =============================================================================
$enrichClvScript = Join-Path $Root "scripts\enrich_graded_workbook_clv.py"
$yesterdayOutDir = Join-Path $Root "outputs\$Yesterday"
if ((Test-Path $enrichClvScript) -and (Test-Path $yesterdayOutDir)) {
    Write-Log "STEP A1c - CLV column enrich ($Yesterday): START"
    Push-Location $Root
    try {
        & py -3.14 -X utf8 $enrichClvScript --scan-dir $yesterdayOutDir
        Write-Log "STEP A1c - CLV column enrich ($Yesterday): OK (see script log for per-file skips)"
    }
    catch {
        Write-Log "STEP A1c - CLV column enrich ($Yesterday): WARN ($($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Log "STEP A1c - CLV column enrich: SKIP (no script or outputs\$Yesterday)"
}

# =============================================================================
# STEP A2 — Build player consistency after grading
# =============================================================================
if ($SkipConsistency) {
    Write-Log "STEP A2 - Player consistency build: SKIPPED (-SkipConsistency)"
}
else {
    Write-Log "STEP A2 - Player consistency build: START"
    $consistencyScript = Join-Path $Root "scripts\build_player_consistency.py"
    Push-Location $Root
    try {
        & py -3.14 $consistencyScript
        $ce = $LASTEXITCODE
        if ($ce -ne 0) {
            Write-Warning "Player consistency build failed — grades may be stale"
            Write-Log "STEP A2 - Player consistency build: FAILED (py exit $ce)"
        }
        else {
            Write-Log "STEP A2 - Player consistency build: OK"
        }
    }
    catch {
        Write-Warning "Player consistency build failed — grades may be stale"
        Write-Log "STEP A2 - Player consistency build: FAILED (exception: $($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
}

# =============================================================================
# STEP B — Archive yesterday's dated outputs (copy-only; keep originals)
# =============================================================================
$YesterdayOut = Join-Path $Root "outputs\$Yesterday"
$ArchiveDir = Join-Path $YesterdayOut "archive"
$archiveFiles = @(
    (Join-Path $YesterdayOut "step8_nba_direction_clean_$Yesterday.xlsx"),
    (Join-Path $YesterdayOut "step8_nba1h_direction_clean_$Yesterday.xlsx"),
    (Join-Path $YesterdayOut "step8_nba1q_direction_clean_$Yesterday.xlsx"),
    (Join-Path $YesterdayOut "step8_soccer_direction_clean_$Yesterday.xlsx"),
    (Join-Path $YesterdayOut "step8_nhl_direction_clean_$Yesterday.xlsx"),
    (Join-Path $YesterdayOut "step6_ranked_wcbb_$Yesterday.xlsx")
)
if (-not (Test-Path $YesterdayOut)) {
    Write-Log "STEP B - Archive yesterday: SKIP (no folder outputs\$Yesterday)"
}
else {
    if (-not (Test-Path $ArchiveDir)) {
        New-Item -ItemType Directory -Path $ArchiveDir -Force | Out-Null
    }
    foreach ($src in $archiveFiles) {
        if (Test-Path $src) {
            $name = Split-Path $src -Leaf
            Copy-Item -LiteralPath $src -Destination (Join-Path $ArchiveDir $name) -Force -ErrorAction SilentlyContinue
        }
    }
    # CBB: anything under outputs\<yesterday>\ matching step6_ranked_cbb*.xlsx
    Get-ChildItem -Path $YesterdayOut -Filter "step6_ranked_cbb*.xlsx" -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $ArchiveDir $_.Name) -Force -ErrorAction SilentlyContinue
    }
    Write-Log "STEP B - Archive yesterday ($Yesterday): OK"
}

# =============================================================================
# STEP C — Full pipeline for today
# =============================================================================
if (-not $SkipPipeline) {
    if ($SkipGameLines) {
        Write-Log "STEP C0 - Fetch game lines: SKIPPED (-SkipGameLines)"
    }
    else {
        Write-Log "STEP C0 - Fetch game lines"
        $gameLinesScript = Join-Path $Root "scripts\fetch_game_lines.py"
        Push-Location $Root
        try {
            & py -3.14 $gameLinesScript --refresh
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Game lines fetch failed - spread data unavailable"
                Write-Log "STEP C0 - Fetch game lines: FAILED (continuing)"
            }
            else {
                Write-Log "STEP C0 - Fetch game lines: OK"
            }
        }
        catch {
            Write-Warning "Game lines fetch failed - spread data unavailable"
            Write-Log "STEP C0 - Fetch game lines: FAILED (exception: $($_.Exception.Message))"
        }
        finally {
            Pop-Location
        }
    }

    # Rolling 1Q/2Q actuals → nba1q table (PropOracle ref DB). Fills holes if grader was skipped or
    # the machine was offline; skips dates that already have CSVs. Safe with daily grader (idempotent).
    if (-not $SkipFetch -and -not $SkipPeriodHistorySync -and $PeriodHistoryLookbackDays -gt 0) {
        Write-Log "STEP C0b - NBA period history sync (lookback=$PeriodHistoryLookbackDays d): START"
        $fetchPeriod = Join-Path $Root "scripts\fetch_nba_period_actuals.py"
        $buildHist = Join-Path $Root "scripts\build_nba1q_history_db.py"
        if (-not (Test-Path $fetchPeriod) -or -not (Test-Path $buildHist)) {
            Write-Log "STEP C0b - NBA period history sync: SKIP (missing script)"
        }
        else {
            $baseDay = [datetime]::ParseExact($Today, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
            $synced = 0
            Push-Location $Root
            try {
                for ($off = 1; $off -le $PeriodHistoryLookbackDays; $off++) {
                    $d = $baseDay.AddDays(-$off).ToString("yyyy-MM-dd")
                    $dayDir = Join-Path $Root "outputs\$d"
                    $p2 = Join-Path $dayDir "actuals_nba2q_$d.csv"
                    $p1 = Join-Path $dayDir "actuals_nba1q_$d.csv"
                    if ((Test-Path $p2) -and (Test-Path $p1)) { continue }
                    if (-not (Test-Path $dayDir)) {
                        New-Item -ItemType Directory -Path $dayDir -Force | Out-Null
                    }
                    Write-Host "  [C0b] Fetching NBA period actuals for $d (missing 1Q/2Q CSV)..." -ForegroundColor DarkCyan
                    & py -3.14 $fetchPeriod --date $d --segment "2Q" --output $p2
                    if ($LASTEXITCODE -ne 0) {
                        Write-Warning "fetch_nba_period_actuals 2Q failed for $d (exit $LASTEXITCODE)"
                    }
                    & py -3.14 $fetchPeriod --date $d --segment "1Q" --output $p1
                    if ($LASTEXITCODE -ne 0) {
                        Write-Warning "fetch_nba_period_actuals 1Q failed for $d (exit $LASTEXITCODE)"
                    }
                    $synced++
                }
                Write-Host "  [C0b] Rebuilding nba1q history DB ($synced day(s) had fetches)..." -ForegroundColor DarkCyan
                & py -3.14 $buildHist
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "build_nba1q_history_db.py failed (exit $LASTEXITCODE) — NBA1H/NBA1Q L5 may be thin"
                    Write-Log "STEP C0b - NBA period history sync: WARN (build_nba1q_history_db exit $LASTEXITCODE)"
                }
                else {
                    Write-Log "STEP C0b - NBA period history sync: OK (filled gaps for $synced day(s))"
                }
            }
            catch {
                Write-Warning "STEP C0b exception: $_"
                Write-Log "STEP C0b - NBA period history sync: FAILED ($($_.Exception.Message))"
            }
            finally {
                Pop-Location
            }
        }
    }
    elseif ($SkipPeriodHistorySync) {
        Write-Log "STEP C0b - NBA period history sync: SKIPPED (-SkipPeriodHistorySync)"
    }
    elseif ($SkipFetch) {
        Write-Log "STEP C0b - NBA period history sync: SKIPPED (-SkipFetch)"
    }

    Write-Log "STEP C - Pipeline ($Today): START"
    $pipeScript = Join-Path $Root "run_pipeline.ps1"
    $pipeArgs = @("-File", $pipeScript, "-Date", $Today)
    if ($EffectiveOddsKey) {
        $pipeArgs += @("-OddsApiKey", $EffectiveOddsKey)
    }
    # Always force a fresh, full slate build during daily runs.
    $pipeArgs += "-ForceAll"
    $pipeArgs += "-SkipCombined"
    $pipeArgs += "-SkipPush"
    Push-Location $Root
    try {
        & pwsh -NoProfile @pipeArgs
        $pe = $LASTEXITCODE
        $combinedToday = Join-Path $Root "outputs\$Today\combined_slate_tickets_$Today.xlsx"
        if ($pe -ne 0) {
            $script:PipelineFailed = $true
            Write-Log "STEP C - Pipeline ($Today): FAILED (pwsh exit $pe)"
            Write-Host "Pipeline reported failure (exit $pe)." -ForegroundColor Red
        }
        elseif (-not $SkipPipeline -and -not (Test-Path $combinedToday) -and -not ($pipeArgs -contains "-SkipCombined")) {
            $script:PipelineFailed = $true
            Write-Log "STEP C - Pipeline ($Today): FAILED (missing $combinedToday)"
            Write-Host "Pipeline finished but combined slate not found under outputs\$Today\" -ForegroundColor Red
        }
        else {
            Write-Log "STEP C - Pipeline ($Today): OK"
            if (-not $AllowMissingSlates) {
                $missingToday = Get-MissingTodaySlateOutputs -RunDate $Today
                if ($missingToday.Count -gt 0) {
                    $script:PipelineFailed = $true
                    Write-Log "STEP C - Pipeline ($Today): FAILED (missing outputs: $($missingToday -join ', '))"
                    Write-Host "Pipeline finished, but required today outputs are missing:" -ForegroundColor Red
                    foreach ($m in $missingToday) {
                        Write-Host "  - $m" -ForegroundColor Red
                    }
                }
            }
        }
    }
    catch {
        $script:PipelineFailed = $true
        Write-Log "STEP C - Pipeline ($Today): FAILED (exception: $($_.Exception.Message))"
        Write-Host "Pipeline exception: $_" -ForegroundColor Red
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Log "STEP C - Pipeline ($Today): SKIPPED (-SkipPipeline)"
}

# =============================================================================
# STEP D — Combined slate for today (explicit; ensures outputs + web)
# =============================================================================
if ($script:PipelineFailed) {
    Write-Log "STEP D - Combined slate: SKIPPED (pipeline failed)"
    Write-Host "Skipping combined slate — fix pipeline first." -ForegroundColor Yellow
} elseif ($SkipPipeline -and -not (Test-Path (Join-Path $Root "outputs\$Today\combined_slate_tickets_$Today.xlsx"))) {
    Write-Log "STEP D - Combined slate: SKIPPED (-SkipPipeline and no existing combined output)"
    Write-Host "Skipping combined slate — pipeline was skipped and no existing output found." -ForegroundColor Yellow
} else {
    Write-Log "STEP D - Combined slate: START"
    $todayOutDir = Join-Path $Root "outputs\$Today"
    if (-not (Test-Path $todayOutDir)) {
        New-Item -ItemType Directory -Path $todayOutDir -Force | Out-Null
    }
    $combinedOut = Join-Path $todayOutDir "combined_slate_tickets_$Today.xlsx"
    Push-Location $Root
    try {
        $pipeScript = Join-Path $Root "run_pipeline.ps1"
        & pwsh -NoProfile -File $pipeScript -Date $Today -CombinedOnly -DQWarnOnly
        $ce = $LASTEXITCODE
        # Success = combined Excel exists; exit code may be non-zero if only ticket_eval HTML failed (non-fatal)
        if (Test-Path $combinedOut) {
            Write-Log "STEP D - Combined slate: OK$(if ($ce -ne 0) { " (ticket_eval warning, exit $ce)" })"
            if ($ce -ne 0) { Write-Warning "Combined slate saved OK but ticket_eval step returned exit $ce (non-fatal)" }
        } elseif ($ce -ne 0) {
            Write-Log "STEP D - Combined slate: FAILED (pwsh exit $ce)"
            Write-Warning "Combined slate failed (exit $ce)"
        } else {
            Write-Log "STEP D - Combined slate: FAILED (output missing)"
            Write-Warning "Combined output missing — expected $combinedOut"
        }
    }
    catch {
        Write-Log "STEP D - Combined slate: FAILED (exception: $($_.Exception.Message))"
        Write-Warning "Combined slate error: $_"
    }
    finally {
        Pop-Location
    }
}

# =============================================================================
# STEP D2 — Copy step8 clean slates to sport root folders (Railway reads these)
# =============================================================================
Write-Log "STEP D2 - Copy Railway slate files to sport roots: START"
$railwayCopies = @(
    @{ Src = "NBA\data\outputs\step8_all_direction_clean.xlsx"; Dst = "NBA\step8_all_direction_clean.xlsx" },
    @{ Src = "Soccer\outputs\step8_soccer_direction_clean.xlsx"; Dst = "Soccer\step8_soccer_direction_clean.xlsx" },
    @{ Src = "MLB\outputs\step8_mlb_direction_clean.xlsx"; Dst = "MLB\step8_mlb_direction_clean.xlsx" }
)
foreach ($rc in $railwayCopies) {
    $srcPath = Join-Path $Root $rc.Src
    $dstPath = Join-Path $Root $rc.Dst
    if (Test-Path $srcPath) {
        Copy-Item -LiteralPath $srcPath -Destination $dstPath -Force
        Write-Log "STEP D2 - Copied $($rc.Src) -> $($rc.Dst)"
    }
    else {
        Write-Log "STEP D2 - SKIP (source missing): $($rc.Src)"
    }
}
Write-Log "STEP D2 - Copy Railway slate files to sport roots: OK"

# =============================================================================
# STEP E — Git commit + push
# =============================================================================
if ($SkipPush) {
    Write-Log "STEP E - Git push: SKIPPED (-SkipPush)"
}
else {
    Write-Log "STEP E - Git push: START"
    $gitLog = Join-Path $Root "logs\git_push_log.txt"
    Push-Location $Root
    try {
        if ($WeeklyAnalysis) {
            $analysisTodayDir = Join-Path $Root "outputs\$Today"
            if (-not (Test-Path $analysisTodayDir)) {
                New-Item -ItemType Directory -Path $analysisTodayDir -Force | Out-Null
            }
            $weeklyReportPath = Join-Path $analysisTodayDir "grader_analysis_$Today.txt"
            Write-Log "STEP E0 - Weekly grader analysis: START"
            $analyzeScript = Join-Path $Root "scripts\analyze_grader.py"
            try {
                & py -3.14 $analyzeScript --output $weeklyReportPath
                $ae = $LASTEXITCODE
                if ($ae -ne 0) {
                    Write-Log "STEP E0 - Weekly grader analysis: FAILED (py exit $ae)"
                }
                else {
                    Write-Log "STEP E0 - Weekly grader analysis: OK"
                    $script:WeeklyAnalysisReport = $weeklyReportPath
                    $synScript = Join-Path $Root "scripts\build_synthetic_graded.py"
                    $consScript = Join-Path $Root "scripts\build_player_consistency.py"
                    Write-Log "STEP E0b - Weekly synthetic + consistency rebuild: START"
                    try {
                        # build_synthetic_graded.py writes synthetic props to data\cache\synthetic_graded.db
                        & py -3.14 $synScript
                        $se = $LASTEXITCODE
                        if ($se -ne 0) {
                            Write-Log "STEP E0b - build_synthetic_graded: FAILED (exit $se)"
                            Write-Warning "build_synthetic_graded.py failed (exit $se)"
                        }
                        else {
                            Write-Log "STEP E0b - build_synthetic_graded: OK"
                        }
                        & py -3.14 $consScript --rebuild --sources all
                        $ce = $LASTEXITCODE
                        if ($ce -ne 0) {
                            Write-Log "STEP E0b - build_player_consistency --sources all: FAILED (exit $ce)"
                            Write-Warning "Player consistency full rebuild failed (exit $ce)"
                        }
                        else {
                            Write-Log "STEP E0b - build_player_consistency --sources all: OK"
                        }
                    }
                    catch {
                        Write-Log "STEP E0b - Weekly synthetic/consistency: FAILED (exception: $($_.Exception.Message))"
                        Write-Warning "Weekly synthetic or consistency rebuild error: $_"
                    }
                }
            }
            catch {
                Write-Log "STEP E0 - Weekly grader analysis: FAILED (exception: $($_.Exception.Message))"
            }
        }

        git -C $Root add -- "outputs/$Today/" "ui_runner/templates/"
        $optionalAdds = @(
            "NBA\step8_all_direction_clean.xlsx",
            "NBA\step8_nba1h_direction_clean.xlsx",
            "NBA\step8_nba1q_direction_clean.xlsx",
            "Soccer\step8_soccer_direction_clean.xlsx",
            "MLB\step8_mlb_direction_clean.xlsx",
            "CBB\step6_ranked_cbb.xlsx",
            "CBB\step6_ranked_wcbb.xlsx",
            "NHL\outputs\step8_nhl_direction_clean.xlsx"
        )
        foreach ($rel in $optionalAdds) {
            $full = Join-Path $Root $rel
            if (Test-Path $full) {
                git -C $Root add -- $rel
            }
        }

        if ($WeeklyAnalysis -and (Test-Path (Join-Path $Root "outputs\$Today\grader_analysis_$Today.txt"))) {
            git -C $Root add -- "outputs/$Today/grader_analysis_$Today.txt"
        }

        $CommitMsg = "Daily slate $Today [auto]"
        $porcelain = git -C $Root status --porcelain 2>$null
        if (-not $porcelain) {
            Write-Host "Git: nothing to commit." -ForegroundColor DarkGray
            Write-Log "STEP E - Git push: OK (nothing to commit)"
        }
        else {
            git -C $Root commit -m $CommitMsg
            if ($LASTEXITCODE -ne 0) {
                Write-Log "STEP E - Git push: FAILED (commit exit $LASTEXITCODE)"
                Write-Warning "Git commit failed — check repo state"
            }
            else {
                try {
                    git -C $Root push origin main
                    if ($LASTEXITCODE -ne 0) {
                        $err = if ($Error.Count -gt 0) { $Error[0].ToString() } else { "unknown" }
                        "$Today - push failed: $err" | Out-File -FilePath $gitLog -Append -Encoding utf8
                        Write-Warning "Git push failed — logged to logs\git_push_log.txt"
                        Write-Log "STEP E - Git push: FAILED (push exit $LASTEXITCODE)"
                    }
                    else {
                        Write-Log "STEP E - Git push: OK"
                    }
                }
                catch {
                    $err = $_.Exception.Message
                    "$Today - push failed: $err" | Out-File -FilePath $gitLog -Append -Encoding utf8
                    Write-Warning "Git push failed — logged to logs\git_push_log.txt"
                    Write-Log "STEP E - Git push: FAILED (exception: $err)"
                }
            }
        }
    }
    finally {
        Pop-Location
    }
}

# =============================================================================
# Monthly model retraining (after STEP E; continues on script failure)
# =============================================================================
if ($MonthlyRetrain) {
    Write-Host "=== Monthly Model Retraining ===" -ForegroundColor Cyan
    Write-Log "MONTHLY - Model retrain: START"
    Push-Location $Root
    try {
        $retrainScripts = @(
            @{ Name = "train_prop_model_nba"; Rel = "scripts\train_prop_model_nba.py" },
            @{ Name = "train_prop_model_cbb"; Rel = "scripts\train_prop_model_cbb.py" },
            @{ Name = "train_prop_model_soccer"; Rel = "scripts\train_prop_model_soccer.py" },
            @{ Name = "train_prop_model_nhl"; Rel = "scripts\train_prop_model_nhl.py" }
        )
        foreach ($rs in $retrainScripts) {
            Write-Log "MONTHLY - $($rs.Name): START"
            $sp = Join-Path $Root $rs.Rel
            try {
                & py -3.14 $sp
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "$($rs.Name) failed (exit $LASTEXITCODE) — continuing; old model files remain valid"
                    Write-Log "MONTHLY - $($rs.Name): FAILED (py exit $LASTEXITCODE)"
                }
                else {
                    Write-Log "MONTHLY - $($rs.Name): OK"
                }
            }
            catch {
                Write-Warning "$($rs.Name) exception: $($_.Exception.Message)"
                Write-Log "MONTHLY - $($rs.Name): FAILED (exception: $($_.Exception.Message))"
            }
        }
        Write-Log "MONTHLY - build_player_consistency --rebuild --sources all: START"
        try {
            $bpc = Join-Path $Root "scripts\build_player_consistency.py"
            & py -3.14 $bpc --rebuild --sources all
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "build_player_consistency failed (exit $LASTEXITCODE)"
                Write-Log "MONTHLY - build_player_consistency --rebuild: FAILED (py exit $LASTEXITCODE)"
            }
            else {
                Write-Log "MONTHLY - build_player_consistency --rebuild: OK"
            }
        }
        catch {
            Write-Log "MONTHLY - build_player_consistency --rebuild: FAILED (exception: $($_.Exception.Message))"
        }
    }
    finally {
        Pop-Location
    }
    Write-Host "Retraining complete" -ForegroundColor Green
    Write-Log "MONTHLY - Model retrain: complete"
}

$dur = (Get-Date) - $script:DailyStart
Write-Log "Daily run complete. Duration: $([int]$dur.TotalMinutes)m $([int]$dur.Seconds)s"
if ($WeeklyAnalysis -and $script:WeeklyAnalysisReport) {
    Write-Log "Weekly grader analysis report: $($script:WeeklyAnalysisReport)"
    Write-Host "Weekly grader analysis report: $($script:WeeklyAnalysisReport)" -ForegroundColor Cyan
}
Write-Log "======== Daily run end ========"

if ($script:PipelineFailed -and -not $SkipPipeline) {
    exit 1
}
exit 0
