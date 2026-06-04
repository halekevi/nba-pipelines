#requires -Version 7.2
<#
.SYNOPSIS
  Daily PropOracle run: grade yesterday, archive dated outputs, run today's full pipeline, combined slate, git push.

.NOTES
  Order: (A1) Refresh historical game logs → (A) Grader for yesterday → (A1b) build_ticket_eval for yesterday → (A1b-sync) grade_history → templates → (A1c) optional CLV Excel columns → (A2) consistency
         → (B) Archive outputs\<yesterday>\ step8 copies → (C0) fetch game lines → (C0b) rolling NBA 1Q/2Q DB sync
         → (C) run_pipeline for today → (D) combined_slate → (E) git commit/push → (E1) optional payout hand CSV pull from Railway
         → (F) optional night poll of historical actuals.
         Tennis: -TennisDate defaults to slate -Date (ET match-day for step8); override when needed.
         Set env PROPORACLE_PAYOUT_EXPORT_URL (e.g. https://<app>.up.railway.app/api/payout/export-log-hand) to merge Railway volume logs into data\payout_samples\payout_log_hand.csv after STEP E.
         Combined slate (STEP D via run_pipeline.ps1) fetches Underdog + DraftKings by default; set PROPORACLE_SKIP_ALT_BOOKS=1 or pass -SkipAltBooks to run_pipeline to disable.
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
    [string]$TennisDate = "",
    [string]$OddsApiKey = "",
    [switch]$ForceAll,
    [switch]$AllowMissingSlates,
    [switch]$SkipPeriodHistorySync,
    [int]$PeriodHistoryLookbackDays = 10,
    [int]$A1TimeoutMinutes = 30,
    [switch]$PollHistoricalActuals,
    [int]$PollPasses = 4,
    [int]$PollIntervalSeconds = 5400,
    [switch]$PollSkip9pmWait,
    [switch]$NoOverwrite,
    [string]$TicketModelMode = "",
    [double]$TicketModelWeight = 0.35,
    [int]$TicketModelTopN = 10
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim())
        }
    }
}
$SportsRoot = Join-Path $Root "Sports"
# WNBA: must match $WNBA_SEASON_START in repo-root run_pipeline.ps1 (parallel job + dated step8 gate).
$WNBA_SEASON_START = "2026-05-01"

# Ensure local cache folder exists
# (excluded from OneDrive, must be created locally)
$CacheDir = Join-Path $Root "data\cache"
if (!(Test-Path $CacheDir)) {
    New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    Write-Host "Created local cache directory: $CacheDir" `
      -ForegroundColor DarkGray
}

# C: drive preflight — xlsx writes and Python temp fail when disk is full.
$cFree = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 1)
if ($cFree -lt 10) {
    Write-Warning "C: drive has only ${cFree} GB free — daily run may fail on xlsx writes"
    Write-Warning "Run scripts/cleanup_c_drive.ps1 before proceeding"
}
if ($cFree -lt 2) {
    Write-Error "C: drive critically low (${cFree} GB) — aborting daily run"
    exit 1
}
if ($cFree -lt 5) {
    $tmpPath = Join-Path $Root ".tmp"
    New-Item -ItemType Directory -Force -Path $tmpPath | Out-Null
    $env:TEMP = $tmpPath
    $env:TMP  = $tmpPath
    Write-Warning "C: low — Python temp redirected to $tmpPath"
}

$script:DailyStart = Get-Date
$script:PipelineFailed = $false
$script:WeeklyAnalysisReport = ""
function Get-TimeStamp { return Get-Date -Format "HH:mm:ss" }

$Today = if ($Date.Trim()) { $Date.Trim() } else { (Get-Date).ToString("yyyy-MM-dd") }
$Yesterday = if ($GradeDate.Trim()) { $GradeDate.Trim() } else { (Get-Date).AddDays(-1).ToString("yyyy-MM-dd") }
# Tennis step8 ET filter + dated filename use the same calendar day as -Date by default.
# Override with -TennisDate "yyyy-MM-dd" when the match-day differs from the pipeline folder date.
$TennisDate = if ($TennisDate -and $TennisDate.Trim()) { $TennisDate.Trim() } else { $Today }
$TicketModelModeEffective = if ($TicketModelMode.Trim()) { $TicketModelMode.Trim().ToLowerInvariant() } elseif ([string]$env:TICKET_MODEL_MODE) { ([string]$env:TICKET_MODEL_MODE).Trim().ToLowerInvariant() } else { "shadow" }
if (@("off", "shadow", "on") -notcontains $TicketModelModeEffective) {
    Write-Warning "Invalid TicketModelMode '$TicketModelModeEffective' (expected off|shadow|on); defaulting to shadow"
    $TicketModelModeEffective = "shadow"
}
$PqControlPercent = 10
if ([string]$env:PROPORACLE_PQ_CONTROL_PERCENT) {
    $tmpPct = 0
    if ([int]::TryParse(([string]$env:PROPORACLE_PQ_CONTROL_PERCENT).Trim(), [ref]$tmpPct)) {
        $PqControlPercent = [Math]::Max(0, [Math]::Min(100, $tmpPct))
    }
}
$PqControlMaxTickets = 4
if ([string]$env:PROPORACLE_PQ_CONTROL_MAX_TICKETS) {
    $tmpCap = 0
    if ([int]::TryParse(([string]$env:PROPORACLE_PQ_CONTROL_MAX_TICKETS).Trim(), [ref]$tmpCap)) {
        $PqControlMaxTickets = [Math]::Max(1, $tmpCap)
    }
}

$LogsDir = Join-Path $Root "logs"
if (!(Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$LogFile = Join-Path $LogsDir "run_daily_$Today.log"

function Write-Log([string]$Message) {
    $line = "[$(Get-TimeStamp)] $Message"
    $line | Tee-Object -FilePath $LogFile -Append
}

function Get-VersionedPath([string]$Path) {
    $dir = Split-Path -Parent $Path
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $ext = [System.IO.Path]::GetExtension($Path)
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $candidate = Join-Path $dir "$name.bak_$stamp$ext"
    $i = 1
    while (Test-Path $candidate) {
        $candidate = Join-Path $dir "$name.bak_${stamp}_$i$ext"
        $i++
    }
    return $candidate
}

function Preserve-ExistingFile([string]$Path, [string]$Reason = "") {
    if (-not $NoOverwrite) { return }
    if (-not (Test-Path $Path)) { return }
    $backup = Get-VersionedPath -Path $Path
    Copy-Item -LiteralPath $Path -Destination $backup -Force -ErrorAction SilentlyContinue
    if ($Reason) {
        Write-Log "NO-OVERWRITE - Preserved '$Path' -> '$backup' ($Reason)"
    }
    else {
        Write-Log "NO-OVERWRITE - Preserved '$Path' -> '$backup'"
    }
}

function Get-CsvDataRowCount([string]$CsvPath) {
    if (-not (Test-Path $CsvPath)) { return 0 }
    try {
        $raw = Import-Csv -Path $CsvPath
        if ($null -eq $raw) { return 0 }
        if ($raw -is [array]) { return $raw.Count }
        return 1
    }
    catch {
        return 0
    }
}

function Get-MissingTodaySlateOutputs {
    param(
        [string]$RunDate,
        # Dated tennis step8 under outputs\<RunDate>\ uses match-day filename (see run_pipeline.ps1 $TennisDate).
        [string]$TennisSlateDate = ""
    )
    $outDir = Join-Path $Root "outputs\$RunDate"
    $tennisDated = if ($TennisSlateDate -and $TennisSlateDate.Trim()) { $TennisSlateDate.Trim() } else { $RunDate }
    $required = @(
        "step8_nba_direction_clean_$RunDate.xlsx",
        "step8_nba1h_direction_clean_$RunDate.xlsx",
        "step8_nba1q_direction_clean_$RunDate.xlsx",
        "step8_nhl_direction_clean_$RunDate.xlsx",
        "step8_soccer_direction_clean_$RunDate.xlsx",
        "step8_mlb_direction_clean_$RunDate.xlsx",
        "step8_tennis_direction_clean_$tennisDated.xlsx"
    )
    # WNBA: run_wnba_pipeline.ps1 publishes outputs/<date>/step8_wnba_direction_clean_<date>.xlsx
    # (same basename pattern as other sports' step8_*_direction_clean_<date>.xlsx).
    if ($RunDate -ge $WNBA_SEASON_START) {
        $required = @($required) + @("step8_wnba_direction_clean_$RunDate.xlsx")
    }
    # 2026 NCAA: WCBB title Sun Apr 5; men's title Mon Apr 6. Expect no WCBB slate from Apr 6+;
    # no men's CBB slate from Apr 7+ — omit from required outputs so daily does not false-fail.
    if ($RunDate -lt "2026-04-07") {
        $required = @($required) + @("step6_ranked_cbb_$RunDate.xlsx")
    }
    if ($RunDate -lt "2026-04-06") {
        $required = @($required) + @("step6_ranked_wcbb_$RunDate.xlsx")
    }
    # Some sports can intentionally skip writing dated copies while still producing
    # valid root clean files used by combined + Railway.
    $fallbackRoots = @{
        # NBA: run_pipeline + step8 also copy dated slates, but a silent Copy-Item miss should not
        # hard-fail the daily if the clean root xlsx in Sports\NBA is present (grader/combined use it).
        "step8_nba_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "NBA\data\outputs\step8_all_direction_clean.xlsx"),
            (Join-Path $SportsRoot "NBA\step8_all_direction_clean.xlsx")
        )
        "step8_nba1h_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "NBA\step8_nba1h_direction_clean.xlsx")
        )
        "step8_nba1q_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "NBA\step8_nba1q_direction_clean.xlsx")
        )
        "step8_nhl_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "NHL\outputs\step8_nhl_direction_clean.xlsx"),
            (Join-Path $SportsRoot "NHL\step8_nhl_direction_clean.xlsx")
        )
        "step8_soccer_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "Soccer\outputs\step8_soccer_direction_clean.xlsx"),
            (Join-Path $SportsRoot "Soccer\step8_soccer_direction_clean.xlsx")
        )
        "step8_mlb_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "MLB\outputs\step8_mlb_direction_clean.xlsx"),
            (Join-Path $SportsRoot "MLB\step8_mlb_direction_clean.xlsx")
        )
        "step8_tennis_direction_clean_$tennisDated.xlsx" = @(
            (Join-Path $SportsRoot "Tennis\outputs\step8_tennis_direction_clean.xlsx"),
            (Join-Path $SportsRoot "Tennis\step8_tennis_direction_clean.xlsx")
        )
        "step8_wnba_direction_clean_$RunDate.xlsx" = @(
            (Join-Path $SportsRoot "WNBA\step8_wnba_direction_clean.xlsx"),
            (Join-Path $SportsRoot "WNBA\outputs\step8_wnba_direction_clean.xlsx")
        )
    }
    $missing = @()
    foreach ($name in $required) {
        $p = Join-Path $outDir $name
        if (Test-Path $p) { continue }
        if ($fallbackRoots.ContainsKey($name)) {
            $resolved = $false
            foreach ($fallback in @($fallbackRoots[$name])) {
                if (Test-Path $fallback) {
                    $resolved = $true
                    break
                }
            }
            if ($resolved) { continue }
        }
        $missing += $name
    }
    return $missing
}

# Python / UTF-8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Log "======== Daily run start (Today=$Today, Yesterday=$Yesterday) ========"
Write-Log "  [Tennis] Using TennisDate: $TennisDate (Today=$Today)"
Write-Log "Ticket model mode: $TicketModelModeEffective (weight=$TicketModelWeight, top_n=$TicketModelTopN)"
Write-Log "PQS control slice: ${PqControlPercent}% (cap=$PqControlMaxTickets, pq=0.0, artifacts only)"
if ($NoOverwrite) {
    Write-Log "NO-OVERWRITE mode enabled (existing files are preserved to *.bak_YYYYMMDD_HHMMSS before updates)"
}

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
        # Incremental: past seasons stay in SQLite; only current season is re-fetched per player.
        # (Do not pass legacy --refresh-current — it forced a full multi-season re-download and was very slow.)
        $a1Proc = Start-Process -FilePath "py" `
            -ArgumentList @("-3.14", "-u", $fetchScript) `
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
$yesterdayCombinedXlsxRoot = Join-Path $Root "combined_slate_tickets_$Yesterday.xlsx"
$yesterdayHasTickets = (Test-Path $yesterdayCombinedXlsx) -or (Test-Path $yesterdayCombinedJson) -or (Test-Path $yesterdayCombinedXlsxRoot)
$yesterdayTixGraded = Join-Path $Root "outputs\$Yesterday\combined_tickets_graded_$Yesterday.xlsx"
if (-not $SkipGrader) {
    $gradedExpected = @(
        (Join-Path $Root "outputs\$Yesterday\graded_nba_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_cbb_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_nhl_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_soccer_$Yesterday.xlsx"),
        (Join-Path $Root "outputs\$Yesterday\graded_mlb_$Yesterday.xlsx")
    )
    # WNBA: run_grader.ps1 fetches actuals + slate_grader, but STEP A must not skip while
    # graded_wnba is still missing if we already have a WNBA step8 for yesterday.
    $yesterdayOutForWnba = Join-Path $Root "outputs\$Yesterday"
    if (Test-Path $yesterdayOutForWnba) {
        $hasWnbaStep8 = @(
            (Get-ChildItem -LiteralPath $yesterdayOutForWnba -Filter "step8_wnba*.xlsx" -File -ErrorAction SilentlyContinue)
        ).Count -gt 0
        if ($hasWnbaStep8) {
            $gradedExpected = @($gradedExpected) + @(
                (Join-Path $Root "outputs\$Yesterday\graded_wnba_$Yesterday.xlsx")
            )
        }
    }
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
        # Grader -Date is the slate/match day; run_grader.ps1 resolves step8 from outputs/(Date - 1) for Tennis.
        $graderDate = $Yesterday
        try {
            & pwsh -NoProfile -File $graderScript -Date $graderDate
            $graderExit = $LASTEXITCODE
            if ($graderExit -ne 0) {
                Write-Warning "Grader failed for $graderDate — check logs (exit $graderExit)"
                Write-Log "STEP A - Grader ($graderDate): FAILED (exit $graderExit)"
                $script:PipelineFailed = $true
            }
            else {
                Write-Log "STEP A - Grader ($graderDate): OK"
                $hotTrackerScript = Join-Path $Root "scripts\hot_players_tracker.py"
                if (Test-Path $hotTrackerScript) {
                    & py -3.14 $hotTrackerScript grade --date $graderDate
                    $htg = $LASTEXITCODE
                    if ($htg -ne 0) {
                        Write-Warning "Hot Players grade failed for $graderDate (non-fatal, exit $htg)"
                        Write-Log "STEP A1 - Hot Players grade ($graderDate): FAILED (py exit $htg)"
                    }
                    else {
                        Write-Log "STEP A1 - Hot Players grade ($graderDate): OK"
                    }
                }
            }
        }
        catch {
            Write-Warning "Grader failed for $graderDate — check logs"
            Write-Log "STEP A - Grader ($graderDate): FAILED (exception: $($_.Exception.Message))"
            $script:PipelineFailed = $true
        }
    }
}
else {
    Write-Log "STEP A - Grader ($Yesterday): SKIPPED (-SkipGrader)"
}

# =============================================================================
# STEP A-track — Model performance + shadow comparison (after grader)
# =============================================================================
if (-not $SkipGrader) {
    Write-Host "=== STEP: Model Performance Tracking ===" -ForegroundColor Cyan
    Write-Log "STEP A-track - Model performance: START"
    Push-Location $Root
    try {
        $trackAcc = Join-Path $Root "scripts\track_prediction_accuracy.py"
        $trackPerf = Join-Path $Root "scripts\track_model_performance.py"
        $compareShadow = Join-Path $Root "scripts\compare_shadow_vs_live.py"
        if (Test-Path $trackAcc) {
            & py -3.14 -X utf8 $trackAcc --days 30
            if ($LASTEXITCODE -ne 0) { Write-Warning "track_prediction_accuracy.py exited $LASTEXITCODE" }
        }
        if (Test-Path $trackPerf) {
            & py -3.14 -X utf8 $trackPerf
            if ($LASTEXITCODE -ne 0) { Write-Warning "track_model_performance.py exited $LASTEXITCODE" }
        }
        if (Test-Path $compareShadow) {
            & py -3.14 -X utf8 $compareShadow --days 7
            if ($LASTEXITCODE -ne 0) { Write-Warning "compare_shadow_vs_live.py exited $LASTEXITCODE" }
        }
        Write-Log "STEP A-track - Model performance: OK"
    }
    catch {
        Write-Warning "Model performance tracking failed: $($_.Exception.Message)"
        Write-Log "STEP A-track - Model performance: WARN ($($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
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
    Write-Log "STEP A1b - Ticket eval HTML ($Yesterday): SKIP (no combined_slate under outputs\$Yesterday\ or repo root)"
}

# =============================================================================
# STEP A1b-sync — grade_history.json → ui_runner/templates (Railway /income fallback)
# build_ticket_eval appends to data/ only; git push (STEP E) must include templates copy.
# =============================================================================
$syncGradeHistoryScript = Join-Path $Root "scripts\sync_grade_history_to_templates.py"
if (Test-Path $syncGradeHistoryScript) {
    Write-Log "STEP A1b-sync - grade_history → templates: START"
    Push-Location $Root
    try {
        & py -3.14 -X utf8 $syncGradeHistoryScript
        $sg = $LASTEXITCODE
        if ($sg -ne 0) {
            Write-Log "STEP A1b-sync - grade_history → templates: WARN (exit $sg; run build_ticket_eval first)"
        }
        else {
            Write-Log "STEP A1b-sync - grade_history → templates: OK"
        }
    }
    catch {
        Write-Log "STEP A1b-sync - grade_history → templates: WARN ($($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Log "STEP A1b-sync - grade_history → templates: SKIP (sync_grade_history_to_templates.py missing)"
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
        $uiConsistencyScript = Join-Path $Root "scripts\build_player_consistency_ui.py"
        if (Test-Path $uiConsistencyScript) {
            & py -3.14 $uiConsistencyScript --min-props 10 --top-n 50
            $cue = $LASTEXITCODE
            if ($cue -ne 0) {
                Write-Warning "Player consistency UI JSON build failed (non-fatal)"
                Write-Log "STEP A2b - Player consistency UI JSON: FAILED (py exit $cue)"
            }
            else {
                Write-Log "STEP A2b - Player consistency UI JSON: OK"
            }
        }
        $hotTrackerScript = Join-Path $Root "scripts\hot_players_tracker.py"
        if (Test-Path $hotTrackerScript) {
            & py -3.14 $hotTrackerScript snapshot --date $Today --limit 8
            $hte = $LASTEXITCODE
            if ($hte -ne 0) {
                Write-Warning "Hot Players snapshot failed (non-fatal, exit $hte)"
                Write-Log "STEP A2c - Hot Players snapshot: FAILED (py exit $hte)"
            }
            else {
                Write-Log "STEP A2c - Hot Players snapshot ($Today): OK"
            }
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
            $archiveTarget = Join-Path $ArchiveDir $name
            if ($NoOverwrite -and (Test-Path $archiveTarget)) {
                $archiveTarget = Get-VersionedPath -Path $archiveTarget
            }
            Copy-Item -LiteralPath $src -Destination $archiveTarget -Force -ErrorAction SilentlyContinue
        }
    }
    # CBB: anything under outputs\<yesterday>\ matching step6_ranked_cbb*.xlsx
    Get-ChildItem -Path $YesterdayOut -Filter "step6_ranked_cbb*.xlsx" -File -ErrorAction SilentlyContinue | ForEach-Object {
        $archiveTarget = Join-Path $ArchiveDir $_.Name
        if ($NoOverwrite -and (Test-Path $archiveTarget)) {
            $archiveTarget = Get-VersionedPath -Path $archiveTarget
        }
        Copy-Item -LiteralPath $_.FullName -Destination $archiveTarget -Force -ErrorAction SilentlyContinue
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

    if ($NoOverwrite) {
        $prePipelineTargets = @(
            (Join-Path $Root "outputs\$Today\combined_slate_tickets_$Today.xlsx"),
            (Join-Path $Root "outputs\$Today\combined_slate_tickets_$Today.json"),
            (Join-Path $Root "ui_runner\templates\tickets_latest.html"),
            (Join-Path $Root "ui_runner\templates\tickets_latest.json"),
            (Join-Path $Root "ui_runner\templates\slate_latest.json"),
            (Join-Path $Root "ui_runner\templates\slate_eval_$Today.html"),
            (Join-Path $Root "ui_runner\templates\ticket_eval_$Today.html"),
            (Join-Path $Root "ui_runner\templates\graded_props_$Today.json")
        )
        foreach ($pt in $prePipelineTargets) {
            Preserve-ExistingFile -Path $pt -Reason "pre-STEP C pipeline snapshot"
        }
    }
    # NHL PP skater cache (API). D-pair refresh (pairings.php, slate teams) runs in run_pipeline.ps1
    # as NHL Step 4b-pre after step4 and before step4b — requires step4 board for --slate-input.
    if ($env:NST_ACCESS_KEY) {
        Write-Host "[NHL] Refreshing NHL PP skater cache (NST D-pairs run in pipeline step 4b-pre)..." -ForegroundColor Cyan
        Write-Log "[NHL] NHL PP cache refresh: START"
        Push-Location $Root
        try {
            & py -3.14 Sports\NHL\scripts\refresh_nst_cache.py --season 20252026 --refresh-pp
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[NHL] WARN: NHL PP cache refresh failed (exit $LASTEXITCODE)" -ForegroundColor Yellow
                Write-Log "[NHL] NHL PP cache refresh: WARN (exit $LASTEXITCODE)"
            }
            else {
                Write-Log "[NHL] NHL PP cache refresh: OK"
            }
        }
        catch {
            Write-Host "[NHL] WARN: NHL PP cache refresh failed" -ForegroundColor Yellow
            Write-Log "[NHL] NHL PP cache refresh: WARN ($($_.Exception.Message))"
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Host "[NHL] WARN: NST_ACCESS_KEY not set — skipping NHL PP cache refresh" -ForegroundColor Yellow
        Write-Log "[NHL] NHL PP cache refresh: SKIP (NST_ACCESS_KEY not set)"
    }

    Write-Log "STEP C - Pipeline ($Today): START"
    $pipeScript = Join-Path $Root "run_pipeline.ps1"
    $pipeArgs = @("-File", $pipeScript, "-Date", $Today, "-TennisDate", $TennisDate)
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
                $missingToday = Get-MissingTodaySlateOutputs -RunDate $Today -TennisSlateDate $TennisDate
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
# STEP C1 — Prop reliability index refresh (used by ticket pool gating)
# =============================================================================
if ($script:PipelineFailed) {
    Write-Log "STEP C1 - Prop reliability index: SKIPPED (pipeline failed)"
}
else {
    $reliabilityScript = Join-Path $Root "scripts\validate_prop_reliability.py"
    if (Test-Path $reliabilityScript) {
        try {
            Write-Log "STEP C1 - Prop reliability index: START"
            & py -3.14 -X utf8 $reliabilityScript --min-n 40 --out-json (Join-Path $Root "data\reports\prop_reliability_latest.json")
            if ($LASTEXITCODE -eq 0) {
                Write-Log "STEP C1 - Prop reliability index: OK"
            }
            else {
                Write-Log "STEP C1 - Prop reliability index: WARN (exit $LASTEXITCODE)"
            }
        }
        catch {
            Write-Log "STEP C1 - Prop reliability index: WARN ($($_.Exception.Message))"
        }
    }
    else {
        Write-Log "STEP C1 - Prop reliability index: SKIP (script missing)"
    }
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
        & pwsh -NoProfile -File $pipeScript -Date $Today -TennisDate $TennisDate -CombinedOnly -DQWarnOnly
        $ce = $LASTEXITCODE
        # Success = combined Excel exists; exit code may be non-zero if only ticket_eval HTML failed (non-fatal)
        if (Test-Path $combinedOut) {
            if ($ce -ne 0) {
                Write-Log "STEP D - Combined slate: OK (workbook written, ticket_eval exit $ce — check graded HTML)"
                Write-Warning "Combined slate saved OK but ticket_eval returned exit $ce"
                # Do NOT set PipelineFailed — artifacts are usable
            } else {
                Write-Log "STEP D - Combined slate: OK"
            }
        } elseif ($ce -ne 0) {
            Write-Log "STEP D - Combined slate: FAILED (pwsh exit $ce, output missing)"
            Write-Warning "Combined slate failed (exit $ce)"
            $script:PipelineFailed = $true
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
# STEP D1 — Ticket-level ML refresh + eval history + ultimate tickets
# Modes:
#   off    -> build EV-only ultimate tickets, skip dataset/train/eval
#   shadow -> build EV-only ultimate tickets, but run dataset/train/eval + append lift history
#   on     -> run dataset/train/eval, then build ultimate tickets with ticket-model rerank;
#             on any model failure, auto-fallback to EV-only for zero-risk output.
# =============================================================================
if ($script:PipelineFailed) {
    Write-Log "STEP D1 - Ticket model refresh/eval: SKIPPED (pipeline failed)"
}
else {
    Write-Log "STEP D1 - Ticket model refresh/eval: START (mode=$TicketModelModeEffective)"
    $ticketDatasetOk = $false
    $ticketTrainOk = $false
    $ticketEvalOk = $false
    $ticketModelAllowedThisRun = $false
    $ticketModeApplied = "off"
    $ticketEvalSummaryPath = Join-Path $Root "data\ml\ticket_model_eval_summary_latest.json"
    $ticketEvalByDatePath = Join-Path $Root "data\ml\ticket_model_eval_by_date.csv"
    $ticketEvalHistoryPath = Join-Path $Root "data\ml\ticket_model_eval_history.csv"
    $uiDataDir = Join-Path $Root "ui_runner\data"
    $uiReportsDir = Join-Path $uiDataDir "reports"
    if (-not (Test-Path $uiDataDir)) { New-Item -ItemType Directory -Path $uiDataDir -Force | Out-Null }
    if (-not (Test-Path $uiReportsDir)) { New-Item -ItemType Directory -Path $uiReportsDir -Force | Out-Null }
    $ticketRunReportPath = Join-Path $uiReportsDir "ticket_model_eval_report_$Today.json"
    $dataMlDir = Join-Path $Root "data\ml"
    if (-not (Test-Path $dataMlDir)) {
        New-Item -ItemType Directory -Path $dataMlDir -Force | Out-Null
    }
    Push-Location $Root
    try {
        $buildDatasetScript = Join-Path $Root "scripts\build_ticket_training_dataset.py"
        $trainTicketScript = Join-Path $Root "scripts\train_ticket_model.py"
        $evalTicketScript = Join-Path $Root "scripts\evaluate_ticket_model.py"
        $ultimateScript = Join-Path $Root "scripts\build_ultimate_tickets.py"

        if ($TicketModelModeEffective -ne "off") {
            if (Test-Path $buildDatasetScript) {
                & py -3.14 -X utf8 $buildDatasetScript --output (Join-Path $Root "data\ml\ticket_training_dataset.csv")
                if ($LASTEXITCODE -eq 0) {
                    $ticketDatasetOk = $true
                    Write-Log "STEP D1a - build_ticket_training_dataset: OK"
                }
                else {
                    Write-Log "STEP D1a - build_ticket_training_dataset: WARN (exit $LASTEXITCODE)"
                }
            }
            else {
                Write-Log "STEP D1a - build_ticket_training_dataset: SKIP (script missing)"
            }

            if ($ticketDatasetOk -and (Test-Path $trainTicketScript)) {
                & py -3.14 -X utf8 $trainTicketScript --input-csv (Join-Path $Root "data\ml\ticket_training_dataset.csv") --target label_cash
                if ($LASTEXITCODE -eq 0) {
                    $ticketTrainOk = $true
                    Write-Log "STEP D1b - train_ticket_model: OK"
                }
                else {
                    Write-Log "STEP D1b - train_ticket_model: WARN (exit $LASTEXITCODE)"
                }
            }
            elseif (-not (Test-Path $trainTicketScript)) {
                Write-Log "STEP D1b - train_ticket_model: SKIP (script missing)"
            }

            if ($ticketTrainOk -and (Test-Path $evalTicketScript)) {
                & py -3.14 -X utf8 $evalTicketScript `
                    --input-csv (Join-Path $Root "data\ml\ticket_training_dataset.csv") `
                    --model (Join-Path $Root "models\ticket_model.pkl") `
                    --features (Join-Path $Root "models\ticket_model_features.json") `
                    --top-n $TicketModelTopN `
                    --weight $TicketModelWeight `
                    --ranking-mode blend `
                    --out-csv $ticketEvalByDatePath `
                    --out-json $ticketEvalSummaryPath
                if ($LASTEXITCODE -eq 0) {
                    $ticketEvalOk = $true
                    Write-Log "STEP D1c - evaluate_ticket_model: OK"
                }
                else {
                    Write-Log "STEP D1c - evaluate_ticket_model: WARN (exit $LASTEXITCODE)"
                }
            }
            elseif (-not (Test-Path $evalTicketScript)) {
                Write-Log "STEP D1c - evaluate_ticket_model: SKIP (script missing)"
            }

            if ($ticketEvalOk -and (Test-Path $ticketEvalSummaryPath)) {
                try {
                    $summaryRaw = Get-Content -Raw -LiteralPath $ticketEvalSummaryPath | ConvertFrom-Json
                    $histRow = [PSCustomObject]@{
                        run_ts_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
                        slate_date = $Today
                        mode_requested = $TicketModelModeEffective
                        top_n = [int]$summaryRaw.top_n
                        blend_weight = [double]$summaryRaw.blend_weight
                        rows_decided = [int]$summaryRaw.rows_decided
                        date_count = [int]$summaryRaw.date_count
                        delta_cash_rate = [double]$summaryRaw.by_date_avg_delta.delta_cash_rate
                        delta_avg_net_10 = [double]$summaryRaw.by_date_avg_delta.delta_avg_net_10
                        delta_total_net_10 = [double]$summaryRaw.by_date_avg_delta.delta_total_net_10
                        top_swapped_count = [double]$summaryRaw.by_date_avg_delta.top_swapped_count
                        avg_pred_p_cash = [double]$summaryRaw.overall.avg_pred_p_cash
                    }
                    if (Test-Path $ticketEvalHistoryPath) {
                        $histRow | Export-Csv -LiteralPath $ticketEvalHistoryPath -Append -NoTypeInformation -Encoding UTF8
                    }
                    else {
                        $histRow | Export-Csv -LiteralPath $ticketEvalHistoryPath -NoTypeInformation -Encoding UTF8
                    }
                    Write-Log "STEP D1d - Ticket eval history append: OK -> $ticketEvalHistoryPath"
                }
                catch {
                    Write-Log "STEP D1d - Ticket eval history append: WARN ($($_.Exception.Message))"
                }
            }
        }
        else {
            Write-Log "STEP D1a-D1d - Ticket model train/eval: SKIPPED (mode=off)"
        }

        # Enable model rerank only in explicit ON mode and only if train+eval succeeded this run.
        if (
            $TicketModelModeEffective -eq "on" -and
            $ticketDatasetOk -and
            $ticketTrainOk -and
            $ticketEvalOk -and
            (Test-Path (Join-Path $Root "models\ticket_model.pkl")) -and
            (Test-Path (Join-Path $Root "models\ticket_model_features.json"))
        ) {
            $ticketModelAllowedThisRun = $true
        }

        if (Test-Path $ultimateScript) {
            if ($ticketModelAllowedThisRun) {
                & py -3.14 -X utf8 $ultimateScript --date $Today --mode balanced --ticket-model on --ticket-model-weight $TicketModelWeight
                if ($LASTEXITCODE -eq 0) {
                    $ticketModeApplied = "on"
                    Write-Log "STEP D1e - build_ultimate_tickets: OK (ticket-model on)"
                }
                else {
                    Write-Log "STEP D1e - build_ultimate_tickets: WARN (ticket-model on exit $LASTEXITCODE); fallback EV-only"
                    & py -3.14 -X utf8 $ultimateScript --date $Today --mode balanced --ticket-model off
                    if ($LASTEXITCODE -eq 0) {
                        $ticketModeApplied = "off"
                        Write-Log "STEP D1e - build_ultimate_tickets: OK (fallback EV-only)"
                    }
                    else {
                        Write-Log "STEP D1e - build_ultimate_tickets: WARN (fallback EV-only exit $LASTEXITCODE)"
                    }
                }
            }
            else {
                & py -3.14 -X utf8 $ultimateScript --date $Today --mode balanced --ticket-model off
                if ($LASTEXITCODE -eq 0) {
                    $ticketModeApplied = "off"
                    if ($TicketModelModeEffective -eq "on") {
                        Write-Log "STEP D1e - build_ultimate_tickets: OK (EV-only fallback due to model stage failure)"
                    }
                    else {
                        Write-Log "STEP D1e - build_ultimate_tickets: OK (EV-only)"
                    }
                }
                else {
                    Write-Log "STEP D1e - build_ultimate_tickets: WARN (EV-only exit $LASTEXITCODE)"
                }
            }
        }
        else {
            Write-Log "STEP D1e - build_ultimate_tickets: SKIP (script missing)"
        }

        # Persist a concise run report in dated outputs (auto-staged by STEP E).
        try {
            $reportObj = [PSCustomObject]@{
                run_ts_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
                slate_date = $Today
                mode_requested = $TicketModelModeEffective
                mode_applied = $ticketModeApplied
                model_stage = [PSCustomObject]@{
                    dataset_ok = $ticketDatasetOk
                    train_ok = $ticketTrainOk
                    eval_ok = $ticketEvalOk
                    model_allowed = $ticketModelAllowedThisRun
                }
                eval_summary_path = $ticketEvalSummaryPath
                eval_by_date_path = $ticketEvalByDatePath
                eval_history_path = $ticketEvalHistoryPath
            }
            if (-not (Test-Path (Split-Path -Parent $ticketRunReportPath))) {
                New-Item -ItemType Directory -Path (Split-Path -Parent $ticketRunReportPath) -Force | Out-Null
            }
            $reportObj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ticketRunReportPath -Encoding UTF8
            Write-Log "STEP D1f - Ticket model run report: OK -> $ticketRunReportPath"
        }
        catch {
            Write-Log "STEP D1f - Ticket model run report: WARN ($($_.Exception.Message))"
        }

        # Edge quality report (props + tickets + model eval row for date when available)
        $edgeQualityScript = Join-Path $Root "scripts\build_edge_quality_report.py"
        if (Test-Path $edgeQualityScript) {
            try {
                & py -3.14 -X utf8 $edgeQualityScript --date $Today --out-dir (Join-Path $Root "outputs\$Today")
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "STEP D1g - Edge quality report: OK"
                }
                else {
                    Write-Log "STEP D1g - Edge quality report: WARN (exit $LASTEXITCODE)"
                }
            }
            catch {
                Write-Log "STEP D1g - Edge quality report: WARN ($($_.Exception.Message))"
            }
        }
        else {
            Write-Log "STEP D1g - Edge quality report: SKIP (script missing)"
        }

        # Trusted prop stratification board (all categories; excludes UNRELIABLE buckets).
        $stratBoardScript = Join-Path $Root "scripts\build_prop_stratification_board.py"
        if (Test-Path $stratBoardScript) {
            try {
                & py -3.14 -X utf8 $stratBoardScript --out-dir $uiDataDir --min-n 30 --top-n 300
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "STEP D1g2 - Prop stratification board: OK"
                }
                else {
                    Write-Log "STEP D1g2 - Prop stratification board: WARN (exit $LASTEXITCODE)"
                }
            }
            catch {
                Write-Log "STEP D1g2 - Prop stratification board: WARN ($($_.Exception.Message))"
            }
        }
        else {
            Write-Log "STEP D1g2 - Prop stratification board: SKIP (script missing)"
        }

        # Prop population state report: current pool states + historical old-vs-new edge-floor backtest.
        $popStateScript = Join-Path $Root "scripts\build_prop_population_state_report.py"
        if (Test-Path $popStateScript) {
            try {
                Write-Log "STEP D1g3 - Prop population state report: START"
                & py -3.14 -X utf8 $popStateScript `
                    --date $Today `
                    --backtest-from "2026-02-19" `
                    --backtest-to $Yesterday `
                    --out-dir $uiReportsDir
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "STEP D1g3 - Prop population state report: OK"
                }
                else {
                    Write-Log "STEP D1g3 - Prop population state report: WARN (exit $LASTEXITCODE)"
                }
            }
            catch {
                Write-Log "STEP D1g3 - Prop population state report: WARN ($($_.Exception.Message))"
            }
        }
        else {
            Write-Log "STEP D1g3 - Prop population state report: SKIP (script missing)"
        }

        $trackPerf = Join-Path $Root "scripts\track_model_performance.py"
        if (Test-Path $trackPerf) {
            try {
                Write-Host "  [D1g3b] NBA1H AUC monitor" -ForegroundColor DarkGray
                Write-Log "STEP D1g3b - NBA1H AUC monitor: START"
                & py -3.14 -X utf8 $trackPerf --nba1h-monitor --date $Yesterday
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "STEP D1g3b - NBA1H AUC monitor: OK"
                }
                else {
                    Write-Log "STEP D1g3b - NBA1H AUC monitor: WARN (exit $LASTEXITCODE)"
                }
            }
            catch {
                Write-Log "STEP D1g3b - NBA1H AUC monitor: WARN ($($_.Exception.Message))"
            }
        }
        else {
            Write-Log "STEP D1g3b - NBA1H AUC monitor: SKIP (script missing)"
        }

        $pipelineReadScript = Join-Path $Root "scripts\enrich_pipeline_read_fields.py"
        if (Test-Path $pipelineReadScript) {
            try {
                Write-Log "STEP D1g4 - Pipeline read-field audit: START"
                & py -3.14 -X utf8 $pipelineReadScript --date $Today --out-dir $uiReportsDir
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "STEP D1g4 - Pipeline read-field audit: OK"
                }
                else {
                    Write-Log "STEP D1g4 - Pipeline read-field audit: WARN (exit $LASTEXITCODE)"
                }
            }
            catch {
                Write-Log "STEP D1g4 - Pipeline read-field audit: WARN ($($_.Exception.Message))"
            }
        }
        else {
            Write-Log "STEP D1g4 - Pipeline read-field audit: SKIP (script missing)"
        }

        # Optional PQ control artifact (small pq0 slice) for drift tracking.
        # This does not overwrite tickets_latest/slate_latest and is kept for offline analysis only.
        if ($PqControlPercent -gt 0) {
            $combinedScript = Join-Path $Root "scripts\combined_slate_tickets.py"
            if (Test-Path $combinedScript) {
                try {
                    $outDir = Join-Path $Root "outputs\$Today"
                    if (-not (Test-Path $outDir)) {
                        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
                    }
                    $controlOut = Join-Path $outDir "combined_slate_tickets_control_pq0_$Today.xlsx"
                    $controlTickets = [Math]::Max(1, [Math]::Min($PqControlMaxTickets, [int][Math]::Floor(40 * ($PqControlPercent / 100.0))))
                    $candidate = @{
                        nba = @((Join-Path $Root "outputs\$Today\step8_nba_direction_clean_$Today.xlsx"), (Join-Path $SportsRoot "NBA\data\outputs\step8_all_direction_clean.xlsx"))
                        nhl = @((Join-Path $Root "outputs\$Today\step8_nhl_direction_clean_$Today.xlsx"), (Join-Path $SportsRoot "NHL\outputs\step8_nhl_direction_clean.xlsx"))
                        soccer = @((Join-Path $Root "outputs\$Today\step8_soccer_direction_clean_$Today.xlsx"), (Join-Path $SportsRoot "Soccer\outputs\step8_soccer_direction_clean.xlsx"))
                        mlb = @((Join-Path $Root "outputs\$Today\step8_mlb_direction_clean_$Today.xlsx"), (Join-Path $SportsRoot "MLB\step8_mlb_direction_clean.xlsx"), (Join-Path $SportsRoot "MLB\outputs\step8_mlb_direction_clean.xlsx"))
                        tennis = @((Join-Path $Root "outputs\$Today\step8_tennis_direction_clean_$TennisDate.xlsx"), (Join-Path $SportsRoot "Tennis\outputs\step8_tennis_direction_clean.xlsx"))
                        nba1q = @((Join-Path $Root "outputs\$Today\step8_nba1q_direction_clean_$Today.xlsx"), (Join-Path $SportsRoot "NBA\step8_nba1q_direction_clean.xlsx"))
                        nba1h = @((Join-Path $Root "outputs\$Today\step8_nba1h_direction_clean_$Today.xlsx"), (Join-Path $SportsRoot "NBA\step8_nba1h_direction_clean.xlsx"))
                        cbb = @((Join-Path $SportsRoot "CBB\step6_ranked_cbb.xlsx"))
                    }
                    $resolved = @{}
                    foreach ($k in $candidate.Keys) {
                        foreach ($p in $candidate[$k]) {
                            if (Test-Path $p) { $resolved[$k] = $p; break }
                        }
                    }
                    if (-not $resolved.ContainsKey("nba")) {
                        Write-Log "STEP D1h - PQ control slice: SKIP (NBA step8 missing)"
                    }
                    else {
                        $controlArgs = @(
                            "-3.14", "-X", "utf8", $combinedScript,
                            "--date", $Today,
                            "--nba", $resolved["nba"],
                            "--output", $controlOut,
                            "--tiers", "A,B,C,D",
                            "--min-hit-rate", "0.45",
                            "--min-edge", "-0.25",
                            "--max-tickets", "$controlTickets",
                            "--ticket-gen-starts", "32",
                            "--nba-structured-variants", "4",
                            "--min-prop-quality", "0.0"
                        )
                        foreach ($opt in @("nhl", "soccer", "mlb", "tennis", "nba1q", "nba1h", "cbb")) {
                            if ($resolved.ContainsKey($opt)) {
                                $controlArgs += @("--$opt", $resolved[$opt])
                            }
                        }
                        & py @controlArgs
                        if ($LASTEXITCODE -eq 0 -and (Test-Path $controlOut)) {
                            Write-Log "STEP D1h - PQ control slice: OK -> $controlOut"
                        }
                        else {
                            Write-Log "STEP D1h - PQ control slice: WARN (exit $LASTEXITCODE)"
                        }
                    }
                }
                catch {
                    Write-Log "STEP D1h - PQ control slice: WARN ($($_.Exception.Message))"
                }
            }
            else {
                Write-Log "STEP D1h - PQ control slice: SKIP (combined_slate_tickets.py missing)"
            }
        }
        else {
            Write-Log "STEP D1h - PQ control slice: SKIP (disabled; PROPORACLE_PQ_CONTROL_PERCENT<=0)"
        }
    }
    catch {
        Write-Log "STEP D1 - Ticket model refresh/eval: WARN (exception: $($_.Exception.Message))"
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
    @{ Src = "outputs\$Today\mlb\step8_mlb_direction_clean.xlsx"; Dst = "MLB\step8_mlb_direction_clean.xlsx" },
    @{ Src = "MLB\outputs\step8_mlb_direction_clean.xlsx"; Dst = "MLB\step8_mlb_direction_clean.xlsx" },
    @{ Src = "Tennis\outputs\step8_tennis_direction_clean.xlsx"; Dst = "Tennis\step8_tennis_direction_clean.xlsx" }
)
foreach ($rc in $railwayCopies) {
    $srcPath = Join-Path $Root $rc.Src
    $dstPath = Join-Path $Root $rc.Dst
    if (Test-Path $srcPath) {
        Preserve-ExistingFile -Path $dstPath -Reason "pre-STEP D2 Railway copy"
        Copy-Item -LiteralPath $srcPath -Destination $dstPath -Force
        Write-Log "STEP D2 - Copied $($rc.Src) -> $($rc.Dst)"
    }
    else {
        Write-Log "STEP D2 - SKIP (source missing): $($rc.Src)"
    }
}
Write-Log "STEP D2 - Copy Railway slate files to sport roots: OK"

# =============================================================================
# STEP D2b — Ensure dated step8 snapshots exist (for historical tier analysis)
# Keeps true Line + Standard Line boards per day under outputs\<date>\.
# =============================================================================
Write-Log "STEP D2b - Dated step8 snapshot backfill: START"
$todayOutDirForSnapshots = Join-Path $Root "outputs\$Today"
if (-not (Test-Path $todayOutDirForSnapshots)) {
    New-Item -ItemType Directory -Path $todayOutDirForSnapshots -Force | Out-Null
}
$datedStep8Copies = @(
    @{
        SrcCandidates = @(
            (Join-Path $SportsRoot "NBA\data\outputs\step8_all_direction_clean.xlsx"),
            (Join-Path $SportsRoot "NBA\step8_all_direction_clean.xlsx")
        )
        Dst = (Join-Path $todayOutDirForSnapshots "step8_nba_direction_clean_$Today.xlsx")
    },
    @{
        SrcCandidates = @((Join-Path $SportsRoot "NBA\step8_nba1h_direction_clean.xlsx"))
        Dst = (Join-Path $todayOutDirForSnapshots "step8_nba1h_direction_clean_$Today.xlsx")
    },
    @{
        SrcCandidates = @((Join-Path $SportsRoot "NBA\step8_nba1q_direction_clean.xlsx"))
        Dst = (Join-Path $todayOutDirForSnapshots "step8_nba1q_direction_clean_$Today.xlsx")
    }
)
foreach ($cp in $datedStep8Copies) {
    $srcResolved = $null
    foreach ($cand in @($cp.SrcCandidates)) {
        if (Test-Path $cand) {
            $srcResolved = $cand
            break
        }
    }
    if ($null -ne $srcResolved) {
        Preserve-ExistingFile -Path $cp.Dst -Reason "pre-STEP D2b dated snapshot copy"
        Copy-Item -LiteralPath $srcResolved -Destination $cp.Dst -Force
        Write-Log "STEP D2b - Copied $(Split-Path $srcResolved -Leaf) -> $(Split-Path $cp.Dst -Leaf)"
    }
    else {
        Write-Log "STEP D2b - SKIP (source missing for $(Split-Path $cp.Dst -Leaf))"
    }
}
Write-Log "STEP D2b - Dated step8 snapshot backfill: OK"

# =============================================================================
# STEP D-ME – Rebuild matchup edge JSON for all sports (today's slate)
# =============================================================================
$meScript = Join-Path $Root "scripts\build_matchup_edge_json.py"
if (Test-Path $meScript) {
    Write-Log "STEP D-ME - Matchup edge rebuild: START"
    Push-Location $Root
    try {
        & py -3.14 -X utf8 $meScript --sport all
        if ($LASTEXITCODE -ne 0) {
            Write-Log "STEP D-ME - Matchup edge rebuild: WARN (exit $LASTEXITCODE)"
        }
        else {
            Write-Log "STEP D-ME - Matchup edge rebuild: OK"
        }
    }
    catch {
        Write-Log "STEP D-ME - Matchup edge rebuild: WARN ($($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Log "STEP D-ME - Matchup edge rebuild: SKIP (script missing)"
}

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

        # mobile/www: grader copies graded_props + slate_eval here; many scripts read mobile/www not templates
        git -C $Root add -- "outputs/$Today/" "ui_runner/templates/" "mobile/www/"
        git -C $Root add -- "ui_runner/templates/*_matchup_edge.json"
        git -C $Root add -- "mobile/www/data/*_matchup_edge.json"
        $optionalAdds = @(
            "NBA\step8_all_direction_clean.xlsx",
            "NBA\step8_nba1h_direction_clean.xlsx",
            "NBA\step8_nba1q_direction_clean.xlsx",
            "Soccer\step8_soccer_direction_clean.xlsx",
            "MLB\step8_mlb_direction_clean.xlsx",
            "Tennis\step8_tennis_direction_clean.xlsx",
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
        $ticketMlArtifacts = @(
            "data\ml\ticket_model_eval_history.csv",
            "data\ml\ticket_model_eval_by_date.csv",
            "data\ml\ticket_model_eval_summary_latest.json"
        )
        foreach ($rel in $ticketMlArtifacts) {
            $full = Join-Path $Root $rel
            if (Test-Path $full) {
                git -C $Root add -- $rel
            }
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
# STEP E1 — Merge payout hand log from Railway (persistent /app/data volume)
# =============================================================================
# Set PROPORACLE_PAYOUT_EXPORT_URL to your deployed app, e.g.:
#   https://<your-service>.up.railway.app/api/payout/export-log-hand
# Railway: add a Volume on the PropORACLE service with mount path /app/data (see ui_runner/app.py DATA_ROOT).
$payoutExportUrl = [string]$env:PROPORACLE_PAYOUT_EXPORT_URL
if ($payoutExportUrl -and $payoutExportUrl.Trim().Length -gt 0) {
    Write-Log "STEP E1 - Payout hand log sync from Railway: START"
    $samplesDir = Join-Path $Root "data\payout_samples"
    if (-not (Test-Path $samplesDir)) {
        New-Item -ItemType Directory -Path $samplesDir -Force | Out-Null
    }
    $tmpRail = Join-Path $samplesDir "payout_log_hand.railway_tmp.csv"
    $localHand = Join-Path $samplesDir "payout_log_hand.csv"
    $mergeScript = Join-Path $Root "scripts\merge_payout_log_hand.py"
    try {
        Invoke-WebRequest -Uri $payoutExportUrl.Trim() -OutFile $tmpRail -UseBasicParsing
        if (-not (Test-Path $mergeScript)) {
            Write-Log "STEP E1 - Payout hand log sync: FAILED (scripts\merge_payout_log_hand.py missing)"
        }
        elseif (-not (Test-Path $tmpRail)) {
            Write-Log "STEP E1 - Payout hand log sync: FAILED (download missing)"
        }
        else {
            & py -3.14 $mergeScript --local $localHand --remote $tmpRail
            if ($LASTEXITCODE -eq 0) {
                Remove-Item -LiteralPath $tmpRail -Force -ErrorAction SilentlyContinue
                Write-Log "STEP E1 - Payout hand log sync: OK -> $localHand"
            }
            else {
                Write-Log "STEP E1 - Payout hand log sync: FAILED (merge exit $LASTEXITCODE)"
            }
        }
    }
    catch {
        Write-Log "STEP E1 - Payout hand log sync: WARN ($($_.Exception.Message))"
    }
}
else {
    Write-Log "STEP E1 - Payout hand log sync: SKIP (set env PROPORACLE_PAYOUT_EXPORT_URL to https://.../api/payout/export-log-hand)"
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
                    -ArgumentList @("-3.14", "-X", "utf8", "-u", $fetchScriptPoll) `
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
# schtasks /Create /TN "PropORACLE_NBA_LateFetch" /TR "powershell.exe -ExecutionPolicy Bypass -NoProfile -File <REPO>\scripts\run_nba_late_fetch.ps1" /SC DAILY /ST 11:00 /F
# =============================================================================
$NowHour = (Get-Date).Hour
if ($NowHour -ge 10) {
    Write-Host "[LATE_FETCH] Re-fetching all sports (append only, no overwrites)..." -ForegroundColor Cyan
    Write-Log "[NBA_LATE_FETCH] Hour=$NowHour >= 10: late slate refresh (all sports step1 --append + full pipeline -SkipFetch)"

    $NBADir = Join-Path $SportsRoot "NBA"
    $lateNbaOutDir = Join-Path $Root "outputs\$Today\nba"
    if (-not (Test-Path -LiteralPath $lateNbaOutDir)) {
        New-Item -ItemType Directory -Force -Path $lateNbaOutDir | Out-Null
    }
    $lateNbaArgs = @(
        # Gentler late-fetch anti-403 settings.
        "--league_id", "7",
        "--game_mode", "pickem",
        "--per_page", "250",
        "--max_pages", "3",
        "--retries", "6",
        "--sleep", "2.0",
        "--cooldown_seconds", "180",
        "--max_cooldowns", "4",
        "--jitter_seconds", "14.0",
        "--append",
        "--date", $Today,
        "--output", (Join-Path $Root "outputs\$Today\nba\step1_pp_props_today.csv")
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

    $NHLDir = Join-Path $SportsRoot "NHL"
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

    $SoccerDir = Join-Path $SportsRoot "Soccer"
    Push-Location $SoccerDir
    try {
        & py -3.14 ".\scripts\step1_fetch_prizepicks_soccer.py" "--append" "--date" "$Today" "--output" "outputs\step1_soccer_props.csv"
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[NBA_LATE_FETCH] Soccer step1 failed (exit $LASTEXITCODE) — continuing"
        Write-Log "[NBA_LATE_FETCH] WARN: Soccer step1 exit $LASTEXITCODE"
    }

    Write-Host "[MLB] Fetching MLB props (same API fetcher as NBA, league_id=2)..." -ForegroundColor Cyan
    $NBADir = Join-Path $SportsRoot "NBA"
    $MLBDir = Join-Path $SportsRoot "MLB"
    $mlbLateOut = Join-Path $Root "outputs\$Today\mlb\step1_mlb_props.csv"
    $mlbLateDir = Split-Path $mlbLateOut -Parent
    if (-not (Test-Path $mlbLateDir)) { New-Item -ItemType Directory -Force -Path $mlbLateDir | Out-Null }
    Push-Location $NBADir
    try {
        & py -3.14 -u ".\scripts\step1_fetch_prizepicks_api.py" `
            "--league_id" "2" `
            "--game_mode" "pickem" `
            "--per_page" "250" `
            "--max_pages" "5" `
            "--sleep" "2.0" `
            "--cooldown_seconds" "90" `
            "--max_cooldowns" "3" `
            "--jitter_seconds" "10.0" `
            "--append" `
            "--allow-nearest-future" `
            "--date" "$Today" `
            "--output" $mlbLateOut
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        $mlbOut = $mlbLateOut
        if (-not (Test-Path $mlbOut)) { $mlbOut = Join-Path $MLBDir "step1_mlb_props.csv" }
        $mlbRows = Get-CsvDataRowCount -CsvPath $mlbOut
        if ($mlbRows -gt 0) {
            Write-Warning "[NBA_LATE_FETCH] MLB step1 failed (exit $LASTEXITCODE), but fallback rows are present ($mlbRows) - continuing"
            Write-Log "[NBA_LATE_FETCH] WARN: MLB step1 exit $LASTEXITCODE (fallback rows=$mlbRows)"
        }
        else {
            Write-Warning "[NBA_LATE_FETCH][HIGH] MLB step1 failed (exit $LASTEXITCODE) and no fallback rows are available; continuing other sports"
            Write-Log "[NBA_LATE_FETCH][HIGH] MLB step1 exit $LASTEXITCODE with no fallback rows"
        }
    }

    $wnbaLatePs1 = Join-Path $Root "scripts\run_wnba_pipeline.ps1"
    if (Test-Path -LiteralPath $wnbaLatePs1) {
        Write-Host "[LATE_FETCH] Fetching WNBA props..." -ForegroundColor Cyan
        & pwsh -NoProfile -File $wnbaLatePs1 -Date $Today -Step1Only
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "[NBA_LATE_FETCH] WNBA step1 failed (exit $LASTEXITCODE) — continuing"
            Write-Log "[NBA_LATE_FETCH] WARN: WNBA step1 exit $LASTEXITCODE"
        }
    }

    $pipeScript = Join-Path $Root "run_pipeline.ps1"
    if (Test-Path $pipeScript) {
        & pwsh -NoProfile -File $pipeScript -Date $Today -TennisDate $TennisDate -SkipFetch
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

# =============================================================================
# STEP G — Mobile data push
# =============================================================================
Write-Log "STEP G - Mobile data push: START"
$pushMobileScript = Join-Path $Root "scripts\push_mobile_data.py"
if (Test-Path $pushMobileScript) {
    try {
        & py -3.14 $pushMobileScript
        if ($LASTEXITCODE -eq 0) {
            Write-Log "STEP G - Mobile data push: OK"
        }
        else {
            Write-Log "STEP G - Mobile data push: WARN (exit $LASTEXITCODE)"
        }
    }
    catch {
        Write-Log "STEP G - Mobile data push: WARN ($($_.Exception.Message))"
    }
}
else {
    Write-Log "STEP G - Mobile data push: SKIP (script missing)"
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
