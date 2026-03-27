#requires -Version 7.2
<#
.SYNOPSIS
  Daily PropOracle run: grade yesterday, archive dated outputs, run today's full pipeline, combined slate, git push.

.NOTES
  Order: (A1) Refresh historical game logs → (A) Grader for yesterday → (A2) consistency
         → (B) Archive outputs\<yesterday>\ step8 copies → (C0) fetch game lines → (C) run_pipeline for today
         → (D) combined_slate → (E) git commit/push.
         Use -SkipFetch to skip A1. -SkipGameLines skips C0. -WeeklyAnalysis runs synthetic + full consistency rebuild after analyze_grader.
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
    [switch]$ForceAll
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
        & py -3.14 $fetchScript --refresh-current
        $fe = $LASTEXITCODE
        if ($fe -ne 0) {
            Write-Warning "fetch_historical_actuals.py exited $fe — continuing (see logs\fetch_errors.log)"
            Write-Log "STEP A1 - Historical actuals refresh: WARN (exit $fe)"
        }
        else {
            Write-Log "STEP A1 - Historical actuals refresh: OK"
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
if (-not $SkipGrader) {
    $gradedMarker = Join-Path $Root "outputs\$Yesterday\graded_nba_$Yesterday.xlsx"
    if (Test-Path $gradedMarker) {
        Write-Host "Grader already run for $Yesterday — skipping" -ForegroundColor DarkYellow
        Write-Log "STEP A - Grader ($Yesterday): SKIPPED (graded_nba exists)"
    }
    else {
        Write-Log "STEP A - Grader ($Yesterday): START"
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

    Write-Log "STEP C - Pipeline ($Today): START"
    $pipeScript = Join-Path $Root "run_pipeline.ps1"
    $pipeArgs = @("-File", $pipeScript, "-Date", $Today)
    if ($EffectiveOddsKey) {
        $pipeArgs += @("-OddsApiKey", $EffectiveOddsKey)
    }
    if ($ForceAll) {
        $pipeArgs += "-ForceAll"
    }
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
        & pwsh -NoProfile -File $pipeScript -Date $Today -CombinedOnly
        $ce = $LASTEXITCODE
        if ($ce -ne 0) {
            Write-Log "STEP D - Combined slate: FAILED (pwsh exit $ce)"
            Write-Warning "Combined slate failed (exit $ce)"
        } elseif (-not (Test-Path $combinedOut)) {
            Write-Log "STEP D - Combined slate: FAILED (output missing)"
            Write-Warning "Combined output missing — expected $combinedOut"
        } else {
            Write-Log "STEP D - Combined slate: OK"
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
# STEP E — Git commit + push
# =============================================================================
if ($SkipPush) {
    Write-Log "STEP E - Git push: SKIPPED (-SkipPush)"
}
else {
    Write-Log "STEP E - Git push: START"
    $gitLog = Join-Path $Root "git_push_log.txt"
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
            "NBA\data\outputs\step8_all_direction_clean.xlsx",
            "NBA\step8_nba1h_direction_clean.xlsx",
            "NBA\step8_nba1q_direction_clean.xlsx",
            "Soccer\outputs\step8_soccer_direction_clean.xlsx",
            "CBB\step6_ranked_cbb.xlsx",
            "CBB\step6_ranked_wcbb.xlsx",
            "NHL\step8_nhl_direction_clean.xlsx"
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
                        Write-Warning "Git push failed — logged to git_push_log.txt"
                        Write-Log "STEP E - Git push: FAILED (push exit $LASTEXITCODE)"
                    }
                    else {
                        Write-Log "STEP E - Git push: OK"
                    }
                }
                catch {
                    $err = $_.Exception.Message
                    "$Today - push failed: $err" | Out-File -FilePath $gitLog -Append -Encoding utf8
                    Write-Warning "Git push failed — logged to git_push_log.txt"
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
