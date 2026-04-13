#requires -Version 7.2
<#
.SYNOPSIS
  Daily PropOracle run: grade yesterday, archive dated outputs, run today's full pipeline, combined slate, git push.

.NOTES
  Order: (A1) Refresh historical game logs → (A) Grader for yesterday → (A1b) build_ticket_eval for yesterday → (A1c) optional CLV Excel columns → (A2) consistency
         → (B) Archive outputs\<yesterday>\ step8 copies → (C0) fetch game lines → (C0b) rolling NBA 1Q/2Q DB sync
         → (C) run_pipeline for today → (D) combined_slate → (E) git commit/push → (F) optional night poll of historical actuals.
         Use -SkipFetch to skip A1 and C0b. -SkipGameLines skips C0. -SkipPeriodHistorySync skips C0b only.
         Use -PollHistoricalActuals to re-run fetch_historical_actuals.py every 90 min (4 passes) after 21:00 ET (see -PollSkip9pmWait).
         -WeeklyAnalysis runs synthetic + full consistency rebuild after analyze_grader.
         -MonthlyRetrain after STEP E runs all four prop ML trainers + full consistency rebuild (logs OK/FAILED, continues on failure).
  NCAA 2026: WCBB slate not required from 2026-04-06; men's CBB not required from 2026-04-07 (see Get-MissingTodaySlateOutputs).
  $Root = parent of scripts\ (repo root).
  STEP C calls repo-root run_pipeline.ps1; step 8 Python entrypoints live under each sport's scripts\ folder (see run_pipeline.ps1 for Join-Path $Root "<Sport>\scripts\step8_*.py").
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
    [int]$A1TimeoutMinutes = 30,
    [switch]$PollHistoricalActuals,
    [int]$PollPasses = 4,
    [int]$PollIntervalSeconds = 5400,
    [switch]$PollSkip9pmWait
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
        "step8_nhl_direction_clean_$RunDate.xlsx",
        "step8_soccer_direction_clean_$RunDate.xlsx",
        "step8_mlb_direction_clean_$RunDate.xlsx"
    )
    # 2026 NCAA: WCBB title Sun Apr 5; men's title Mon Apr 6. Expect no WCBB slate from Apr 6+;
    # no men's CBB slate from Apr 7+ — omit from required outputs so daily does not false-fail.
    if ($RunDate -lt "2026-04-07") {
        $required = @($required) + @("step6_ranked_cbb_$RunDate.xlsx")
    }
    if ($RunDate -lt "2026-04-06") {
        $required = @($required) + @("step6_ranked_wcbb_$RunDate.xlsx")
    }
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
$yesterdayCombinedXlsx = Join-Path $Root "outputs\$Yesterday\combined_slate_tickets_$Yesterday.xlsx"
$yesterdayCombinedJson = Join-Path $Root "outputs\$Yesterday\combined_slate_tickets_$Yesterday.json"
$yesterdayHasTickets = (Test-Path $yesterdayCombinedXlsx) -or (Test-Path $yesterdayCombinedJson)
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
    $needCombinedTicketWorkbook = $yesterdayHasTickets -and -not (Test-Path $yesterdayTixGraded)
    if ($missingGraded.Count -eq 0 -and -not $needCombinedTicketWorkbook) {
        Write-Host "Grader outputs already present for $Yesterday — skipping" -ForegroundColor DarkYellow
        Write-Log "STEP A - Grader ($Yesterday): SKIPPED (all graded outputs present; combined ticket workbook OK)"
    }
    else {
        if ($needCombinedTicketWorkbook -and $missingGraded.Count -eq 0) {
            Write-Host "Re-running grader for ${Yesterday}: combined ticket graded workbook missing" -ForegroundColor DarkYellow
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
if ($yesterdayHasTickets) {
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
    Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): SKIP (no outputs\$Yesterday\combined_slate_tickets_$Yesterday.xlsx or .json)"
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
# STEP A1d — Goblin/Demon payout curve fit + combo reference JSON
# =============================================================================
$fitPayoutScript = Join-Path $Root "utils\fit_payout_curve.py"
$comboTableScript = Join-Path $Root "scripts\write_combo_table_latest.py"
Write-Log "STEP A1d - Payout curve / combo table: START"
Push-Location $Root
try {
    if (Test-Path $fitPayoutScript) {
        & py -3.14 -X utf8 $fitPayoutScript --min-obs 10
        $fe = $LASTEXITCODE
        if ($fe -eq 2) {
            Write-Log "STEP A1d - fit_payout_curve: SKIP (not enough observations yet)"
        }
        elseif ($fe -ne 0) {
            Write-Log "STEP A1d - fit_payout_curve: WARN (exit $fe)"
        }
        else {
            Write-Log "STEP A1d - fit_payout_curve: OK"
        }
    }
    else {
        Write-Log "STEP A1d - fit_payout_curve: SKIP (script missing)"
    }
    if (Test-Path $comboTableScript) {
        & py -3.14 -X utf8 $comboTableScript
        $ce = $LASTEXITCODE
        if ($ce -ne 0) {
            Write-Log "STEP A1d - write_combo_table_latest: WARN (exit $ce)"
        }
        else {
            Write-Log "STEP A1d - write_combo_table_latest: OK"
        }
    }
    else {
        Write-Log "STEP A1d - write_combo_table_latest: SKIP (script missing)"
    }
}
catch {
    Write-Log "STEP A1d - Payout curve / combo table: WARN ($($_.Exception.Message))"
}
finally {
    Pop-Location
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
                    $periodTargets = @(
                        @{ Seg = "1Q"; Out = (Join-Path $dayDir "actuals_nba1q_$d.csv") },
                        @{ Seg = "2Q"; Out = (Join-Path $dayDir "actuals_nba2q_$d.csv") },
                        @{ Seg = "3Q"; Out = (Join-Path $dayDir "actuals_nba3q_$d.csv") },
                        @{ Seg = "4Q"; Out = (Join-Path $dayDir "actuals_nba4q_$d.csv") },
                        @{ Seg = "1H"; Out = (Join-Path $dayDir "actuals_nba1h_$d.csv") },
                        @{ Seg = "2H"; Out = (Join-Path $dayDir "actuals_nba2h_$d.csv") }
                    )
                    $missing = @($periodTargets | Where-Object { -not (Test-Path $_.Out) })
                    if ($missing.Count -eq 0) { continue }
                    if (-not (Test-Path $dayDir)) {
                        New-Item -ItemType Directory -Path $dayDir -Force | Out-Null
                    }
                    Write-Host "  [C0b] Fetching NBA period actuals for $d ($($missing.Count) segment(s) missing)..." -ForegroundColor DarkCyan
                    foreach ($t in $missing) {
                        & py -3.14 $fetchPeriod --date $d --segment $t.Seg --output $t.Out
                        if ($LASTEXITCODE -ne 0) {
                            Write-Warning "fetch_nba_period_actuals $($t.Seg) failed for $d (exit $LASTEXITCODE)"
                        }
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

    # Weekly: append resolved ESPN IDs from latest unmatched dump into manual map.
    if ((Get-Date).DayOfWeek -eq "Monday") {
        Write-Log "[SOCCER] Weekly batch ID resolve (Monday): START"
        Push-Location $Root
        try {
            & py -3.14 "Soccer/scripts/batch_append_soccer_manual_map.py" --latest-unmatched
            if ($LASTEXITCODE -ne 0) {
                Write-Log "[SOCCER] Weekly batch ID resolve: WARN (exit $LASTEXITCODE)"
            }
            else {
                Write-Log "[SOCCER] Weekly batch ID resolve: OK"
            }
        }
        catch {
            Write-Log "[SOCCER] Weekly batch ID resolve: FAILED ($($_.Exception.Message))"
        }
        finally {
            Pop-Location
        }
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
            # CBB deactivated - season over (April 2026)
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
# STEP F — Night polling: historical actuals (safe with finalized-game guard in Python)
# =============================================================================
if ($PollHistoricalActuals -and -not $SkipFetch) {
    Write-Log "STEP F - Historical actuals poll: START ($PollPasses passes, interval ${PollIntervalSeconds}s)"
    $fetchScriptPoll = Join-Path $Root "scripts\fetch_historical_actuals.py"
    if (-not (Test-Path $fetchScriptPoll)) {
        Write-Log "STEP F - Historical actuals poll: SKIP (fetch_historical_actuals.py missing)"
    }
    else {
        $tzEt = $null
        foreach ($tzId in @("America/New_York", "Eastern Standard Time")) {
            try {
                $tzEt = [System.TimeZoneInfo]::FindSystemTimeZoneById($tzId)
                break
            }
            catch {
            }
        }
        if ($tzEt -and -not $PollSkip9pmWait) {
            $nowEt = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tzEt)
            $today9pmUnspec = [DateTime]::new($nowEt.Year, $nowEt.Month, $nowEt.Day, 21, 0, 0, [DateTimeKind]::Unspecified)
            $today9pmUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($today9pmUnspec, $tzEt)
            $nowUtc = [DateTime]::UtcNow
            if ($nowUtc -lt $today9pmUtc) {
                $waitSec = [int][Math]::Ceiling(($today9pmUtc - $nowUtc).TotalSeconds)
                if ($waitSec -gt 0) {
                    Write-Log "STEP F - Poll: waiting $waitSec s until 21:00 ET ($($today9pmUnspec.ToString('yyyy-MM-dd')))"
                    Start-Sleep -Seconds $waitSec
                }
            }
        }
        elseif (-not $tzEt -and -not $PollSkip9pmWait) {
            Write-Log "STEP F - Poll: WARN (could not resolve ET timezone — starting passes immediately)"
        }

        Push-Location $Root
        try {
            $pollTimeoutSec = [Math]::Max(120, $A1TimeoutMinutes * 60)
            for ($pi = 0; $pi -lt $PollPasses; $pi++) {
                if ($pi -gt 0) {
                    Write-Log "STEP F - Poll: sleep ${PollIntervalSeconds}s before pass $($pi + 1)/$PollPasses"
                    Start-Sleep -Seconds $PollIntervalSeconds
                }
                Write-Host "[poll] Running actuals fetch pass $($pi + 1)/$PollPasses" -ForegroundColor Cyan
                Write-Log "STEP F - Poll: fetch_historical_actuals pass $($pi + 1)/$PollPasses"
                $pollProc = Start-Process -FilePath "py" `
                    -ArgumentList @("-3.14", "-X", "utf8", "-u", $fetchScriptPoll, "--refresh-current") `
                    -NoNewWindow -PassThru -WorkingDirectory $Root
                $pollDone = $pollProc.WaitForExit($pollTimeoutSec * 1000)
                if (-not $pollDone) {
                    Write-Warning "[poll] fetch_historical_actuals pass $($pi + 1) exceeded timeout (${pollTimeoutSec}s)"
                    Write-Log "STEP F - Poll pass $($pi + 1): WARN (timeout ${pollTimeoutSec}s)"
                    try {
                        Stop-Process -Id $pollProc.Id -Force -ErrorAction SilentlyContinue
                    }
                    catch {
                    }
                }
                else {
                    $pEx = $pollProc.ExitCode
                    if ($pEx -ne 0) {
                        Write-Warning "[poll] Actuals fetch pass $($pi + 1) exited $pEx"
                        Write-Log "STEP F - Poll pass $($pi + 1): WARN (exit $pEx)"
                    }
                    else {
                        Write-Log "STEP F - Poll pass $($pi + 1): OK"
                    }
                }
            }
        }
        catch {
            Write-Log "STEP F - Historical actuals poll: FAILED (exception: $($_.Exception.Message))"
            Write-Warning "STEP F poll exception: $($_.Exception.Message)"
        }
        finally {
            Pop-Location
        }
        Write-Log "STEP F - Historical actuals poll: complete"
    }
}
elseif ($PollHistoricalActuals -and $SkipFetch) {
    Write-Log "STEP F - Historical actuals poll: SKIPPED (-SkipFetch)"
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

# =============================================================================
# Late slate refresh — PrizePicks posts NBA props mid-morning (often ~10–11 ET).
# 7AM daily may have a thin NBA board; scripts\run_nba_late_fetch.ps1 (11AM task) re-fetches
# NBA (append) plus NHL/Soccer/MLB (overwrite), then full pipeline -SkipFetch (see schtasks below).
# If you run run_daily.ps1 manually after ~10:00 local, the same multi-sport refresh runs here.
# =============================================================================
# Register once (working form — no nested quotes needed when path has no spaces):
# schtasks /Create /TN "PropORACLE_NBA_LateFetch" /TR "powershell.exe -ExecutionPolicy Bypass -NoProfile -File C:\Users\halek\OneDrive\Desktop\PropORACLE\scripts\run_nba_late_fetch.ps1" /SC DAILY /ST 11:00 /F
# =============================================================================
$NowHour = (Get-Date).Hour
if ($NowHour -ge 10) {
    Write-Host "[LATE_FETCH] Re-fetching all sports (append only, no overwrites)..." -ForegroundColor Cyan
    Write-Log "[NBA_LATE_FETCH] Hour=$NowHour >= 10: late slate refresh (all sports step1 --append + full pipeline -SkipFetch)"

    $NBADir = Join-Path $Root "NBA"
    $lateNbaArgs = @(
        "--league_id", "7",
        "--game_mode", "pickem",
        "--per_page", "250",
        "--max_pages", "5",
        "--sleep", "2.0",
        "--cooldown_seconds", "90",
        "--max_cooldowns", "3",
        "--jitter_seconds", "10.0",
        "--append",
        "--output", "data\outputs\step1_pp_props_today.csv"
    )
    Push-Location $NBADir
    try {
        & py -3.14 ".\scripts\step1_fetch_prizepicks_api.py" @lateNbaArgs
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[NBA_LATE_FETCH] NBA step1 failed (exit $LASTEXITCODE) — continuing other sports"
        Write-Log "[NBA_LATE_FETCH] WARN: NBA step1 exit $LASTEXITCODE"
    }

    $NHLDir = Join-Path $Root "NHL"
    Push-Location $NHLDir
    try {
        & py -3.14 ".\scripts\step1_fetch_prizepicks_nhl.py" "--append" "--output" "outputs\step1_nhl_props.csv"
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[NBA_LATE_FETCH] NHL step1 failed (exit $LASTEXITCODE) — continuing"
        Write-Log "[NBA_LATE_FETCH] WARN: NHL step1 exit $LASTEXITCODE"
    }

    $SoccerDir = Join-Path $Root "Soccer"
    Push-Location $SoccerDir
    try {
        & py -3.14 ".\scripts\step1_fetch_prizepicks_soccer.py" "--append" "--output" "outputs\step1_soccer_props.csv"
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[NBA_LATE_FETCH] Soccer step1 failed (exit $LASTEXITCODE) — continuing"
        Write-Log "[NBA_LATE_FETCH] WARN: Soccer step1 exit $LASTEXITCODE"
    }

    $MLBDir = Join-Path $Root "MLB"
    Push-Location $MLBDir
    try {
        & py -3.14 ".\scripts\step1_fetch_prizepicks_mlb.py" "--append" "--output" "step1_mlb_props.csv"
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[NBA_LATE_FETCH] MLB step1 failed (exit $LASTEXITCODE) — continuing"
        Write-Log "[NBA_LATE_FETCH] WARN: MLB step1 exit $LASTEXITCODE"
    }

    $pipeScript = Join-Path $Root "run_pipeline.ps1"
    if (Test-Path $pipeScript) {
        & pwsh -NoProfile -File $pipeScript -SkipFetch
        if ($LASTEXITCODE -eq 0) {
            Write-Log "[NBA_LATE_FETCH] OK (full pipeline -SkipFetch)"
        }
        else {
            Write-Warning "[NBA_LATE_FETCH] pipeline exited $LASTEXITCODE"
            Write-Log "[NBA_LATE_FETCH] WARN: pipeline exit $LASTEXITCODE"
        }
    }
    else {
        Write-Warning "[NBA_LATE_FETCH] run_pipeline.ps1 missing at $pipeScript"
        Write-Log "[NBA_LATE_FETCH] WARN: run_pipeline.ps1 missing"
    }
}
else {
    Write-Host "[NBA_LATE_FETCH] Hour=$NowHour < 10, skipping NBA re-fetch (use 11AM task: PropORACLE_NBA_LateFetch)" -ForegroundColor DarkGray
    Write-Log "[NBA_LATE_FETCH] Hour=$NowHour < 10: skipped (scheduled late fetch runs separately)"
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
