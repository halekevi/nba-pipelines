param(
    [string]$Date = ((Get-Date).AddDays(-1).ToString("yyyy-MM-dd")),
    # After copying to ui_runner/graded_slate/<Date>/, commit and push (for CI/Railway).
    [switch]$PushGradedSlate
)

$Root = Split-Path $PSScriptRoot -Parent
$SportsRoot = Join-Path $Root "Sports"
$DateDir = Join-Path $Root "outputs\$Date"
$CanonicalDateDir = Join-Path $DateDir "canonical"

$TicketsFileFrozenCanonical = Join-Path $CanonicalDateDir "combined_slate_tickets_${Date}_to_grade_tomorrow.xlsx"
$TicketsFileFrozen = Join-Path $DateDir "combined_slate_tickets_${Date}_to_grade_tomorrow.xlsx"
$TicketsFileXlsxCanonical = Join-Path $CanonicalDateDir "combined_slate_tickets_$Date.xlsx"
$TicketsFileXlsx = Join-Path $DateDir "combined_slate_tickets_$Date.xlsx"
$TicketsFileJsonCanonical = Join-Path $CanonicalDateDir "combined_slate_tickets_$Date.json"
$TicketsFileJson = Join-Path $DateDir "combined_slate_tickets_$Date.json"
$TicketsFileJsonUiData = Join-Path $Root "ui_runner\data\combined_slate_tickets_$Date.json"
$TicketsFile = if (Test-Path $TicketsFileFrozenCanonical) { $TicketsFileFrozenCanonical } elseif (Test-Path $TicketsFileFrozen) { $TicketsFileFrozen } elseif (Test-Path $TicketsFileXlsxCanonical) { $TicketsFileXlsxCanonical } elseif (Test-Path $TicketsFileXlsx) { $TicketsFileXlsx } elseif (Test-Path $TicketsFileJsonUiData) { $TicketsFileJsonUiData } elseif (Test-Path $TicketsFileJsonCanonical) { $TicketsFileJsonCanonical } elseif (Test-Path $TicketsFileJson) { $TicketsFileJson } else { $TicketsFileXlsx }
$NBAActuals  = Join-Path $DateDir "actuals_nba_$Date.csv"
$NBA1HActuals = Join-Path $DateDir "actuals_nba1h_$Date.csv"
$NBA2HActuals = Join-Path $DateDir "actuals_nba2h_$Date.csv"
$NBA1QActuals = Join-Path $DateDir "actuals_nba1q_$Date.csv"
$NBA2QActuals = Join-Path $DateDir "actuals_nba2q_$Date.csv"
$NBA3QActuals = Join-Path $DateDir "actuals_nba3q_$Date.csv"
$NBA4QActuals = Join-Path $DateDir "actuals_nba4q_$Date.csv"
$CBB1HActuals = Join-Path $DateDir "actuals_cbb1h_$Date.csv"
$CBBActuals  = Join-Path $DateDir "actuals_cbb_$Date.csv"
$WCBBActuals = Join-Path $DateDir "actuals_wcbb_$Date.csv"
$NHLActuals  = Join-Path $DateDir "actuals_nhl_$Date.csv"
$SoccerActuals  = Join-Path $DateDir "actuals_soccer_$Date.csv"
$TennisActuals  = Join-Path $DateDir "actuals_tennis_$Date.csv"
$MlbActuals    = Join-Path $DateDir "actuals_mlb_$Date.csv"
$FetchActualsScript = Join-Path $Root "scripts\fetch_actuals.py"
$FetchTennisActualsScript = Join-Path $Root "scripts\fetch_tennis_actuals.py"
$TennisGraderScript = Join-Path $SportsRoot "Tennis\scripts\tennis_grader.py"
$FetchNBAPeriodActualsScript = Join-Path $Root "scripts\fetch_nba_period_actuals.py"
$BuildNBA1QHistoryScript = Join-Path $Root "scripts\build_nba1q_history_db.py"
$SlateGraderScript = Join-Path $Root "scripts\grading\slate_grader.py"
$CountNbaSlateGradeRowsScript = Join-Path $Root "scripts\count_nba_slate_grade_rows.py"
$CBBFullGraderScript = Join-Path $Root "scripts\grading\grade_cbb_full_slate.py"
$NHLAdvancedGraderScript = Join-Path $Root "scripts\nhl_grader_advanced.py"
$SoccerAdvancedGraderScript = Join-Path $Root "scripts\soccer_grader_advanced.py"
$VoidValidatorScript = Join-Path $Root "scripts\validate_unacceptable_voids.py"
$BuildGradesHtmlScript = Join-Path $Root "scripts\grading\build_grades_html.py"
$BackfillGradedPropsJsonScript = Join-Path $Root "scripts\backfill_graded_props_json.py"
$IngestGradedIncomeScript     = Join-Path $Root "scripts\ingest_graded_to_income_db.py"
$NBABacktestScript = Join-Path $SportsRoot "NBA\scripts\backtest_nba.py"
# Prefer build_ticket_eval.py (multi-date graded merge from leg game_time); fallback to legacy HTML-only builder.
$TicketEvalBuilderScript = Join-Path $Root "scripts\build_ticket_eval.py"
if (-not (Test-Path $TicketEvalBuilderScript)) {
    $TicketEvalBuilderScript = Join-Path $Root "scripts\build_ticket_eval_html.py"
}
$EntryLegGraderScript = Join-Path $Root "scripts\grade_entry_legs.py"

$NBAGradedFile = Join-Path $DateDir "graded_nba_$Date.xlsx"
$CBBGradedFile = Join-Path $DateDir "graded_cbb_$Date.xlsx"
$NHLGradedFile = Join-Path $DateDir "graded_nhl_$Date.xlsx"
$SoccerGradedFile = Join-Path $DateDir "graded_soccer_$Date.xlsx"
$NBA1HGradedFile = Join-Path $DateDir "graded_nba1h_$Date.xlsx"
$NBA1QGradedFile = Join-Path $DateDir "graded_nba1q_$Date.xlsx"
$WCBBGradedFile = Join-Path $DateDir "graded_wcbb_$Date.xlsx"
$TennisGradedFile = Join-Path $DateDir "graded_tennis_$Date.xlsx"
$EvalHtmlFile = Join-Path $DateDir "slate_eval_$Date.html"
$TemplatesDir = Join-Path $Root "ui_runner\templates"
# Local mobile bundle (grades.html loads slate_eval_{date}.html + graded_props_{date}.json from here).
$MobileWwwDir = Join-Path $Root "mobile\www"

# Max size for graded_slate git copies (avoid huge Excel in repo).
$GradedSlateMaxBytes = 5 * 1024 * 1024

function Copy-PropOracleGradedSlateBundle {
    param(
        [string]$RepoRoot,
        [string]$GradeDate,
        [string]$OutputsDir,
        [int]$MaxFileBytes
    )

    $destRoot = Join-Path $RepoRoot "ui_runner\graded_slate\$GradeDate"
    New-Item -ItemType Directory -Force -Path $destRoot | Out-Null

    $names = @(
        "graded_nba_$GradeDate.xlsx",
        "graded_cbb_$GradeDate.xlsx",
        "graded_wcbb_$GradeDate.xlsx",
        "graded_nhl_$GradeDate.xlsx",
        "graded_mlb_$GradeDate.xlsx",
        "graded_soccer_$GradeDate.xlsx",
        "combined_tickets_graded_$GradeDate.xlsx"
    )

    foreach ($name in $names) {
        $src = Join-Path $OutputsDir $name
        if (-not (Test-Path $src)) {
            continue
        }
        $len = (Get-Item -LiteralPath $src).Length
        if ($len -gt $MaxFileBytes) {
            Write-Warning "[GRADER] Skip graded_slate copy (over 5MB): $name ($len bytes)"
            continue
        }
        $dst = Join-Path $destRoot $name
        Copy-Item -LiteralPath $src -Destination $dst -Force
        Write-Host "[GRADER] Deploy copy: $name ($len bytes)" -ForegroundColor Green
    }

    Write-Host "[GRADER] Graded slate bundle -> ui_runner\graded_slate\$GradeDate\" -ForegroundColor Cyan
}

function Run-Py {
    param (
        [string]$Name,
        [string]$WorkingDir,
        [string]$ScriptPath,
        [string[]]$ScriptArgs,
        [switch]$PreferPy314
    )

    Write-Host "`n=== Running $Name ===" -ForegroundColor Cyan

    if (-not (Test-Path $ScriptPath)) {
        Write-Host "  Script not found: $ScriptPath" -ForegroundColor Yellow
        return
    }

    Push-Location $WorkingDir

    try {
        $env:PYTHONUTF8 = "1"
        $env:PYTHONIOENCODING = "utf-8"
        if ($PreferPy314 -and (Get-Command py -ErrorAction SilentlyContinue)) {
            $pyArgs = @("-3.14", "-X", "utf8", $ScriptPath) + $ScriptArgs
            & py @pyArgs
        }
        # Use python first to avoid py launcher version prompts.
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $pyArgs = @($ScriptPath) + $ScriptArgs
            & python -X utf8 @pyArgs
        }
        elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $pyArgs = @("-3", $ScriptPath) + $ScriptArgs
            & py -X utf8 @pyArgs
        }
        else {
            Write-Host "  Python not found in PATH." -ForegroundColor Red
            return
        }
    }
    catch {
        Write-Host "  ERROR running ${Name}: $_" -ForegroundColor Red
    }

    Pop-Location
}

function Resolve-FirstExisting {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return $null
}

Write-Host "`n=====================================" -ForegroundColor Green
Write-Host "   SLATE IQ GRADER RUNNER" -ForegroundColor Green
Write-Host "   Date: $Date"
Write-Host "=====================================`n" -ForegroundColor Green

if (-not (Test-Path $DateDir)) {
    New-Item -ItemType Directory -Path $DateDir -Force | Out-Null
}

# Off-season / deactivated sports: skip fetch + grade and drop stale graded_* for this date (no phantom void rows).
# Default: college only (CBB/WCBB). NBA 1H / 1Q stay enabled - use static step8 + period actuals as usual.
# Re-enable all: set PROPORACLE_GRADER_DISABLED_SPORTS to empty string.
# Temporarily skip period props: PROPORACLE_GRADER_DISABLED_SPORTS=cbb,wcbb,nba1h,nba1q
$GraderDisabledSports = @('cbb', 'wcbb')
if ($null -ne $env:PROPORACLE_GRADER_DISABLED_SPORTS) {
    $envRaw = [string]$env:PROPORACLE_GRADER_DISABLED_SPORTS
    if ($envRaw.Trim() -eq '') {
        $GraderDisabledSports = @()
    }
    else {
        $GraderDisabledSports = @(
            $envRaw.ToLower() -split '[,\s;]+' | Where-Object { $_ }
        ) | Select-Object -Unique
    }
}
function Test-GraderSportDisabled {
    param([Parameter(Mandatory)][string]$SportKey)
    return @($GraderDisabledSports) -contains $SportKey.ToLower().Trim()
}
function Remove-StaleGradedWorkbook {
    param([string]$Path, [string]$Label)
    if (-not $Path) { return }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        Write-Host "[GRADER] Removed stale graded workbook ($Label): $(Split-Path $Path -Leaf)" -ForegroundColor DarkYellow
    }
}
if (@($GraderDisabledSports).Count -gt 0) {
    Write-Host "[GRADER] Disabled sports (skip fetch/grade for this run): $($GraderDisabledSports -join ', ')" -ForegroundColor Yellow
    if (Test-GraderSportDisabled 'cbb') {
        Remove-StaleGradedWorkbook -Path $CBBGradedFile -Label 'cbb'
    }
    if (Test-GraderSportDisabled 'wcbb') {
        Remove-StaleGradedWorkbook -Path $WCBBGradedFile -Label 'wcbb'
    }
    if (Test-GraderSportDisabled 'nba1h') {
        Remove-StaleGradedWorkbook -Path $NBA1HGradedFile -Label 'nba1h'
    }
    if (Test-GraderSportDisabled 'nba1q') {
        Remove-StaleGradedWorkbook -Path $NBA1QGradedFile -Label 'nba1q'
    }
}

# =============================
# Resolve Combined Ticket Grader Path
# =============================
$CombinedTicketGrader = Join-Path $Root "combined_ticket_grader.py"

if (-not (Test-Path $CombinedTicketGrader)) {
    $CombinedTicketGrader = Join-Path $Root "scripts\combined_ticket_grader.py"
}

if (-not (Test-Path $CombinedTicketGrader)) {
    $CombinedTicketGrader = Join-Path $Root "scripts\grading\combined_ticket_grader.py"
}

# =============================
# Fetch Required Actuals First
# =============================
if (Test-Path $FetchActualsScript) {
    Run-Py "Fetch NBA Actuals" $Root $FetchActualsScript @(
        "--sport", "NBA",
        "--date", $Date,
        "--nba-window", "1",
        "--output", $NBAActuals
    )

    if (-not (Test-GraderSportDisabled 'cbb')) {
        Run-Py "Fetch CBB Actuals" $Root $FetchActualsScript @(
            "--sport", "CBB",
            "--date", $Date,
            "--output", $CBBActuals,
            "--window", "0"
        )
    }
    else {
        Write-Host "Skipping Fetch CBB Actuals (sport disabled: cbb)." -ForegroundColor Yellow
    }

    if (-not (Test-GraderSportDisabled 'wcbb')) {
        Run-Py "Fetch WCBB Actuals" $Root $FetchActualsScript @(
            "--sport", "WCBB",
            "--date", $Date,
            "--output", $WCBBActuals,
            "--window", "0"
        )
    }
    else {
        Write-Host "Skipping Fetch WCBB Actuals (sport disabled: wcbb)." -ForegroundColor Yellow
    }

    Run-Py "Fetch NHL Actuals" $Root $FetchActualsScript @(
        "--sport", "NHL",
        "--date", $Date,
        "--output", $NHLActuals
    )

    Run-Py "Fetch Soccer Actuals" $Root $FetchActualsScript @(
        "--sport", "Soccer",
        "--date", $Date,
        "--soccer-window", "1",
        "--output", $SoccerActuals
    )

    if (Test-Path $FetchTennisActualsScript) {
        Run-Py "Fetch Tennis Actuals" $Root $FetchTennisActualsScript @(
            "--date", $Date,
            "--output", $TennisActuals
        )
    }

    if (Test-Path $TennisGraderScript) {
        $TennisStep8Dated = Join-Path $DateDir "step8_tennis_direction_clean_$Date.xlsx"
        $TennisStep8Canonical = Join-Path $DateDir "tennis\step8_tennis_direction_clean.xlsx"
        $TennisStep8Static = Join-Path $SportsRoot "Tennis\outputs\step8_tennis_direction_clean.xlsx"
        $TennisStep8Csv = Join-Path $SportsRoot "Tennis\outputs\step8_tennis_direction.csv"
        $TennisSlateFile = Resolve-FirstExisting @($TennisStep8Canonical, $TennisStep8Dated, $TennisStep8Static, $TennisStep8Csv)
        if (-not $TennisSlateFile) {
            Write-Host "Skipping Tennis grader (no step8 tennis slate; build Tennis pipeline or place step8 under outputs\$Date or Tennis\outputs)." -ForegroundColor Yellow
        }
        else {
            Run-Py "Tennis Grader" $Root $TennisGraderScript @(
                "--date", $Date,
                "--slate", $TennisSlateFile,
                "--output", $TennisGradedFile
            ) -PreferPy314
        }
    }

    if (Test-Path $FetchNBAPeriodActualsScript) {
        if (-not (Test-GraderSportDisabled 'nba1h')) {
            Run-Py "Fetch NBA 1H Actuals" $Root $FetchNBAPeriodActualsScript @(
                "--date", $Date,
                "--segment", "1H",
                "--output", $NBA1HActuals
            )
        }
        else {
            Write-Host "Skipping Fetch NBA 1H Actuals (sport disabled: nba1h)." -ForegroundColor Yellow
        }
        if (-not (Test-GraderSportDisabled 'nba1q')) {
            Run-Py "Fetch NBA 1Q Actuals" $Root $FetchNBAPeriodActualsScript @(
                "--date", $Date,
                "--segment", "1Q",
                "--output", $NBA1QActuals
            )
        }
        else {
            Write-Host "Skipping Fetch NBA 1Q Actuals (sport disabled: nba1q)." -ForegroundColor Yellow
        }
        Run-Py "Fetch NBA 2Q Actuals" $Root $FetchNBAPeriodActualsScript @(
            "--date", $Date,
            "--segment", "2Q",
            "--output", $NBA2QActuals
        )
        Run-Py "Fetch NBA 3Q Actuals" $Root $FetchNBAPeriodActualsScript @(
            "--date", $Date,
            "--segment", "3Q",
            "--output", $NBA3QActuals
        )
        Run-Py "Fetch NBA 4Q Actuals" $Root $FetchNBAPeriodActualsScript @(
            "--date", $Date,
            "--segment", "4Q",
            "--output", $NBA4QActuals
        )
        Run-Py "Fetch NBA 2H Actuals" $Root $FetchNBAPeriodActualsScript @(
            "--date", $Date,
            "--segment", "2H",
            "--output", $NBA2HActuals
        )
        if (-not (Test-GraderSportDisabled 'cbb')) {
            Run-Py "Fetch CBB 1H Actuals (ESPN PBP)" $Root $FetchNBAPeriodActualsScript @(
                "--sport", "CBB",
                "--date", $Date,
                "--segment", "1H",
                "--output", $CBB1HActuals
            )
        }
        else {
            Write-Host "Skipping Fetch CBB 1H Actuals (sport disabled: cbb)." -ForegroundColor Yellow
        }
        if (Test-Path $BuildNBA1QHistoryScript) {
            Write-Host "[NBA1Q DB] Appending Q1/Q2 actuals to proporacle_ref.db..." -ForegroundColor Yellow
            Run-Py "Build NBA1Q History DB" $Root $BuildNBA1QHistoryScript @()
        }
        else {
            Write-Host "[NBA1Q DB] Script not found: $BuildNBA1QHistoryScript" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "NBA period actuals script not found: $FetchNBAPeriodActualsScript" -ForegroundColor Yellow
    }
}
else {
    Write-Host "Fetch actuals script not found: $FetchActualsScript" -ForegroundColor Yellow
}

# =============================
# Grade NBA/CBB + Build HTML
# =============================
$NBAStep8Dated = Join-Path $DateDir "step8_nba_direction_clean_$Date.xlsx"
$NBAStep8Canonical = Join-Path $DateDir "nba\step8_all_direction_clean.xlsx"
$NBAExtractOut = Join-Path $DateDir "nba_slate_extracted_$Date.xlsx"
$NBAStep8Static = Join-Path $SportsRoot "NBA\data\outputs\step8_all_direction_clean.xlsx"
$NBAStep8Static2 = Join-Path $SportsRoot "NBA\step8_all_direction_clean.xlsx"
$ExtractNbaSlateScript = Join-Path $Root "scripts\extract_nba_slate_for_grade_date.py"
$NBAFullForExtract = Resolve-FirstExisting @($NBAStep8Static, $NBAStep8Static2)
if ((Test-Path $ExtractNbaSlateScript) -and (Test-Path $DateDir)) {
    if (-not (Test-Path $NBAStep8Dated) -and -not (Test-Path $NBAExtractOut) -and $NBAFullForExtract) {
        Run-Py "Extract NBA slate rows for $Date" $Root $ExtractNbaSlateScript @(
            "--input", $NBAFullForExtract, "--output", $NBAExtractOut, "--grade-date", $Date
        )
    }
}

# Full Slate on combined_slate_tickets has the full NBA ticket pool (~1k+ rows). step8 date-filter
# often leaves only a handful — export NBA rows so Prop Evaluation matches the ticket workbook.
$ExportNbaFullSlateScript = Join-Path $Root "scripts\export_nba_full_slate_for_grader.py"
$NbaFullSlateForGrade = Join-Path $DateDir "nba_full_slate_for_grade_$Date.xlsx"
if ((Test-Path $ExportNbaFullSlateScript) -and (Test-Path $DateDir)) {
    $combinedCandidates = @(Get-ChildItem -LiteralPath $DateDir -Filter "combined_slate_tickets_${Date}*.xlsx" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($combinedCandidates.Count -gt 0) {
        $pickCombined = $combinedCandidates[0].FullName
        Run-Py "Export NBA Full Slate for grader ($Date)" $Root $ExportNbaFullSlateScript @(
            "--input", $pickCombined,
            "--output", $NbaFullSlateForGrade,
            "--date", $Date
        )
    }
}

$NBASlateFile = Resolve-FirstExisting @(
    $NbaFullSlateForGrade,
    $NBAExtractOut,
    $NBAStep8Canonical,
    $NBAStep8Dated,
    $NBAStep8Static,
    $NBAStep8Static2
)
if ($NBASlateFile) { Write-Host "[GRADER] NBA slate: $(Split-Path $NBASlateFile -Leaf)" -ForegroundColor Cyan }
$CBBSlateXlsx = Resolve-FirstExisting @(
    (Join-Path $DateDir "cbb\step6_ranked_cbb.xlsx"),
    (Join-Path $DateDir "step6_ranked_cbb_$Date.xlsx"),
    (Join-Path $SportsRoot "CBB\step6_ranked_cbb.xlsx")
)
$CBBSlateCsv = Join-Path $DateDir "cbb_slate_extracted_$Date.csv"
$NHLStep8Dated = Join-Path $DateDir "step8_nhl_direction_clean_$Date.xlsx"
$NHLStep8Canonical = Join-Path $DateDir "nhl\step8_nhl_direction_clean.xlsx"
$NHLStep8Static = Join-Path $SportsRoot "NHL\outputs\step8_nhl_direction_clean.xlsx"
$NHLStep8Static2 = Join-Path $SportsRoot "NHL\step8_nhl_direction_clean.xlsx"
$NHLSlateFile = Resolve-FirstExisting @(
    $NHLStep8Canonical,
    $NHLStep8Dated,
    $NHLStep8Static,
    $NHLStep8Static2
)
if ($NHLSlateFile) { Write-Host "[GRADER] NHL slate: $(Split-Path $NHLSlateFile -Leaf)" -ForegroundColor Cyan }

$SoccerStep8Dated = Join-Path $DateDir "step8_soccer_direction_clean_$Date.xlsx"
$SoccerStep8Canonical = Join-Path $DateDir "soccer\step8_soccer_direction_clean.xlsx"
$SoccerStep8Static = Join-Path $SportsRoot "Soccer\outputs\step8_soccer_direction_clean.xlsx"
$SoccerStep8Static2 = Join-Path $SportsRoot "Soccer\step8_soccer_direction_clean.xlsx"
$SoccerSlateFile = Resolve-FirstExisting @(
    $SoccerStep8Canonical,
    $SoccerStep8Dated,
    $SoccerStep8Static,
    $SoccerStep8Static2
)

# Build dated NBA1H/1Q slates from root workbook when archive missing (filters by Game Time == $Date).
$DatedNBA1HPath = Join-Path $DateDir "step8_nba1h_direction_clean_$Date.xlsx"
$DatedNBA1QPath = Join-Path $DateDir "step8_nba1q_direction_clean_$Date.xlsx"
$RootNBA1HPath = Resolve-FirstExisting @((Join-Path $DateDir "nba1h\step8_nba1h_direction_clean.xlsx"), (Join-Path $SportsRoot "NBA\step8_nba1h_direction_clean.xlsx"))
$RootNBA1QPath = Resolve-FirstExisting @((Join-Path $DateDir "nba1q\step8_nba1q_direction_clean.xlsx"), (Join-Path $SportsRoot "NBA\step8_nba1q_direction_clean.xlsx"))
if ((Test-Path $ExtractNbaSlateScript) -and (Test-Path $DateDir)) {
    if (-not (Test-Path $DatedNBA1HPath) -and (Test-Path $RootNBA1HPath)) {
        Run-Py "Extract NBA1H slate for $Date" $Root $ExtractNbaSlateScript @(
            "--input", $RootNBA1HPath, "--output", $DatedNBA1HPath, "--grade-date", $Date
        )
    }
    if (-not (Test-Path $DatedNBA1QPath) -and (Test-Path $RootNBA1QPath)) {
        Run-Py "Extract NBA1Q slate for $Date" $Root $ExtractNbaSlateScript @(
            "--input", $RootNBA1QPath, "--output", $DatedNBA1QPath, "--grade-date", $Date
        )
    }
}

$NBA1HSlateFile = Resolve-FirstExisting @(
    $DatedNBA1HPath,
    (Join-Path $SportsRoot "NBA\step8_nba1h_direction_clean.xlsx")
)
$NBA1QSlateFile = Resolve-FirstExisting @(
    $DatedNBA1QPath,
    (Join-Path $SportsRoot "NBA\step8_nba1q_direction_clean.xlsx")
)
$WCBBSlateFile = Resolve-FirstExisting @(
    (Join-Path $DateDir "step6_ranked_wcbb_$Date.xlsx"),
    (Join-Path $SportsRoot "CBB\step6_ranked_wcbb.xlsx")
)
if ($SoccerSlateFile -and (Test-Path $SoccerSlateFile)) {
    Write-Host "[GRADER] Soccer slate: $(Split-Path $SoccerSlateFile -Leaf)" -ForegroundColor Cyan
}
else {
    Write-Host "Soccer slate: not found (tried outputs\$Date\, Soccer\outputs\, Soccer\)" -ForegroundColor Yellow
}

if ((Test-Path $NBAActuals) -and (Test-Path $NBASlateFile) -and (Test-Path $SlateGraderScript)) {
    Run-Py "Grade NBA Slate" $Root $SlateGraderScript @(
        "--sport", "NBA",
        "--slate", $NBASlateFile,
        "--actuals", $NBAActuals,
        "--output", $NBAGradedFile,
        "--date", $Date
    )

    if (Test-Path $NBABacktestScript) {
        Run-Py "Backtest NBA (Daily)" $Root $NBABacktestScript @(
            "--slate", $NBASlateFile,
            "--actuals", $NBAActuals,
            "--out-dir", (Join-Path $SportsRoot "NBA\data\outputs")
        )

        Run-Py "Backtest NBA (All Historical Actuals)" $Root $NBABacktestScript @(
            "--slate", $NBASlateFile,
            "--out-dir", (Join-Path $SportsRoot "NBA\data\outputs"),
            "--batch-actuals-glob", "**/actuals_nba*.csv"
        )
    }
    else {
        Write-Host "Skipping NBA backtest (script not found: $NBABacktestScript)." -ForegroundColor Yellow
    }
}
else {
    Write-Host "Skipping NBA slate grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

if (Test-GraderSportDisabled 'cbb') {
    Write-Host "Skipping CBB slate grading (sport disabled: cbb - no live lines this season)." -ForegroundColor Yellow
}
elseif ((Test-Path $CBBActuals) -and (Test-Path $CBBSlateCsv) -and (Test-Path $CBBFullGraderScript)) {
    Run-Py "Grade CBB Full Slate" $Root $CBBFullGraderScript @(
        "--slate", $CBBSlateCsv,
        "--actuals", $CBBActuals,
        "--out", $CBBGradedFile
    )
}
elseif ((Test-Path $CBBActuals) -and (Test-Path $CBBSlateXlsx) -and (Test-Path $SlateGraderScript)) {
    Run-Py "Grade CBB Slate" $Root $SlateGraderScript @(
        "--sport", "CBB",
        "--slate", $CBBSlateXlsx,
        "--actuals", $CBBActuals,
        "--output", $CBBGradedFile,
        "--date", $Date
    )
}
else {
    Write-Host "Skipping CBB slate grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

if ((Test-Path $NHLActuals) -and $NHLSlateFile -and (Test-Path $NHLSlateFile) -and (Test-Path $NHLAdvancedGraderScript)) {
    Run-Py "Grade NHL Slate" $Root $NHLAdvancedGraderScript @(
        "--date", $Date,
        "--actuals", $NHLActuals,
        "--slate", $NHLSlateFile,
        "--output-dir", $DateDir
    )
}
else {
    Write-Host "Skipping NHL grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

if ((Test-Path $SoccerActuals) -and $SoccerSlateFile -and (Test-Path $SoccerSlateFile) -and (Test-Path $SoccerAdvancedGraderScript)) {
    if (-not (Test-Path $DateDir)) {
        New-Item -ItemType Directory -Path $DateDir -Force | Out-Null
    }
    Run-Py "Grade Soccer Slate" $Root $SoccerAdvancedGraderScript @(
        "--date", $Date,
        "--actuals", $SoccerActuals,
        "--slate", $SoccerSlateFile,
        "--output-dir", $DateDir
    ) -PreferPy314
    if (-not (Test-Path $SoccerGradedFile)) {
        Write-Warning "Soccer grading produced no output file - check soccer_grader_advanced.py for errors"
    }
    else {
        Write-Host "Soccer grading complete: $SoccerGradedFile" -ForegroundColor Green
    }
}
else {
    Write-Host "Skipping Soccer grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

$MLBActuals   = Join-Path $DateDir "actuals_mlb_$Date.csv"
$MLBGradedFile = Join-Path $DateDir "graded_mlb_$Date.xlsx"
$MlbGradeDateScript = Join-Path $Root "scripts\mlb_grade_date.py"
# mlb_grade_date.py fetches MLB Stats API actuals for slate players and runs nhl_soccer_grader (same as manual flow).
if (Test-Path $MlbGradeDateScript) {
    Run-Py "MLB fetch actuals + grade" $Root $MlbGradeDateScript @(
        "--date", $Date,
        "--output-dir", $DateDir
    )
}
else {
    $MLBStep8Dated = Join-Path $DateDir "step8_mlb_direction_clean_$Date.xlsx"
    $MLBStep8Static = Join-Path $SportsRoot "MLB\outputs\step8_mlb_direction_clean.xlsx"
    $MLBStep8Static2 = Join-Path $SportsRoot "MLB\step8_mlb_direction_clean.xlsx"
    $MLBSlateFile = Resolve-FirstExisting @(
        $MLBStep8Dated,
        $MLBStep8Static,
        $MLBStep8Static2
    )
    if ($MLBSlateFile) { Write-Host "[GRADER] MLB slate: $(Split-Path $MLBSlateFile -Leaf)" -ForegroundColor Cyan }
    if ((Test-Path $MLBActuals) -and $MLBSlateFile -and (Test-Path $MLBSlateFile)) {
        Run-Py "Grade MLB Slate" $Root "scripts\nhl_soccer_grader.py" @(
            "--sport", "MLB",
            "--date", $Date,
            "--actuals", $MLBActuals,
            "--slate", $MLBSlateFile,
            "--output-dir", $DateDir
        )
    }
    else {
        Write-Host "Skipping MLB grading (mlb_grade_date.py missing; no actuals/slate for fallback)." -ForegroundColor Yellow
    }
}

# NBA 1H / 1Q must use period box scores. Do not fall back to full-game actuals_nba_*.csv
# (wrong period totals + avoidable NO_ACTUAL voids). Auto-fetch period CSVs when missing.
if (-not (Test-Path $DateDir)) {
    New-Item -ItemType Directory -Path $DateDir -Force | Out-Null
}
if (Test-Path $FetchNBAPeriodActualsScript) {
    if (-not (Test-GraderSportDisabled 'nba1h')) {
        if ($NBA1HSlateFile -and (Test-Path $NBA1HSlateFile) -and -not (Test-Path $NBA1HActuals)) {
            Run-Py "Fetch NBA 1H Actuals (pre-grade, missing CSV)" $Root $FetchNBAPeriodActualsScript @(
                "--date", $Date,
                "--segment", "1H",
                "--output", $NBA1HActuals
            )
        }
    }
    else {
        Write-Host "Skipping NBA1H actuals fetch (sport disabled: nba1h)." -ForegroundColor Yellow
    }
    if (-not (Test-GraderSportDisabled 'nba1q')) {
        if ($NBA1QSlateFile -and (Test-Path $NBA1QSlateFile) -and -not (Test-Path $NBA1QActuals)) {
            Run-Py "Fetch NBA 1Q Actuals (pre-grade, missing CSV)" $Root $FetchNBAPeriodActualsScript @(
                "--date", $Date,
                "--segment", "1Q",
                "--output", $NBA1QActuals
            )
        }
    }
    else {
        Write-Host "Skipping NBA1Q actuals fetch (sport disabled: nba1q)." -ForegroundColor Yellow
    }
}

if (Test-GraderSportDisabled 'nba1h') {
    Write-Host "Skipping NBA1H grading (sport disabled: nba1h - no live period lines this season)." -ForegroundColor Yellow
}
elseif ($NBA1HSlateFile -and (Test-Path $NBA1HSlateFile) -and (Test-Path $SlateGraderScript) -and (Test-Path $NBA1HActuals)) {
    Run-Py "Grade NBA1H Slate" $Root $SlateGraderScript @(
        "--sport", "NBA",
        "--slate", $NBA1HSlateFile,
        "--actuals", $NBA1HActuals,
        "--output", $NBA1HGradedFile,
        "--date", $Date
    )
}
else {
    Write-Host "Skipping NBA1H grading (missing slate, grader, or actuals_nba1h_$Date.csv after fetch attempt)." -ForegroundColor Yellow
}

$nba1qSlateRowsAfterFilter = -1
if (-not (Test-GraderSportDisabled 'nba1q')) {
    if ($NBA1QSlateFile -and (Test-Path $NBA1QSlateFile) -and (Test-Path $CountNbaSlateGradeRowsScript)) {
        try {
            $env:PYTHONUTF8 = "1"
            $rowOut = & python -X utf8 $CountNbaSlateGradeRowsScript --slate $NBA1QSlateFile --date $Date 2>$null
            if ($rowOut -match '^[0-9]+$') {
                $nba1qSlateRowsAfterFilter = [int]$rowOut
            }
        }
        catch {
            $nba1qSlateRowsAfterFilter = -1
        }
    }
}

if (Test-GraderSportDisabled 'nba1q') {
    Write-Host "Skipping NBA1Q grading (sport disabled: nba1q - no live period lines this season)." -ForegroundColor Yellow
}
elseif ($NBA1QSlateFile -and (Test-Path $NBA1QSlateFile) -and (Test-Path $SlateGraderScript) -and (Test-Path $NBA1QActuals)) {
    if ($nba1qSlateRowsAfterFilter -eq 0) {
        Write-Warning "[GRADER] NBA1Q slate has 0 rows after date filter for $Date (file: $(Split-Path $NBA1QSlateFile -Leaf)). Skipping graded_nba1q write so an existing workbook is not overwritten with empty Box Raw."
    }
    else {
        Run-Py "Grade NBA1Q Slate" $Root $SlateGraderScript @(
            "--sport", "NBA",
            "--slate", $NBA1QSlateFile,
            "--actuals", $NBA1QActuals,
            "--output", $NBA1QGradedFile,
            "--date", $Date
        )
    }
}
else {
    Write-Host "Skipping NBA1Q grading (missing slate, grader, or actuals_nba1q_$Date.csv after fetch attempt)." -ForegroundColor Yellow
}

if (Test-GraderSportDisabled 'wcbb') {
    Write-Host "Skipping WCBB slate grading (sport disabled: wcbb - no live lines this season)." -ForegroundColor Yellow
}
elseif ($WCBBSlateFile -and (Test-Path $WCBBSlateFile) -and (Test-Path $SlateGraderScript)) {
    $WCBBActualsForGrade = if (Test-Path $WCBBActuals) { $WCBBActuals } elseif (Test-Path $CBBActuals) { $CBBActuals } else { $null }
    if ($WCBBActualsForGrade) {
        Run-Py "Grade WCBB Slate" $Root $SlateGraderScript @(
            "--sport", "CBB",
            "--slate", $WCBBSlateFile,
            "--actuals", $WCBBActualsForGrade,
            "--output", $WCBBGradedFile,
            "--date", $Date
        )
    }
    else {
        Write-Host "Skipping WCBB grading (no actuals_wcbb or actuals_cbb CSV)." -ForegroundColor Yellow
    }
}
else {
    Write-Host "Skipping WCBB grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

# =============================
# Validate unacceptable VOIDs (allow NO_DATA + DNP)
# =============================
if (Test-Path $VoidValidatorScript) {
    $VoidArgs = @(
        "--date", $Date,
        "--out-dir", (Join-Path $Root "data\reports\void_validator"),
        "--accepted-void-token", "NO_DATA",
        "--accepted-void-token", "DNP",
        "--accepted-void-token", "NO_ACTUAL",
        "--accepted-void-token", "NO_LINE",
        "--accepted-void-token", "PUSH"
    )
    foreach ($gf in @(
        $NBAGradedFile,
        $CBBGradedFile,
        $WCBBGradedFile,
        $NHLGradedFile,
        $MLBGradedFile,
        $SoccerGradedFile,
        $NBA1HGradedFile,
        $NBA1QGradedFile,
        $TennisGradedFile
    )) {
        if ($gf -and (Test-Path $gf)) {
            $VoidArgs += @("--graded", $gf)
        }
    }
    if ($VoidArgs.Count -gt 8) {
        Run-Py "Validate unacceptable VOIDs" $Root $VoidValidatorScript $VoidArgs
    }
}
else {
    Write-Host "Skipping VOID validator (validate_unacceptable_voids.py not found)." -ForegroundColor Yellow
}

if (Test-Path $BuildGradesHtmlScript) {
    if (-not (Test-Path $TemplatesDir)) {
        New-Item -ItemType Directory -Path $TemplatesDir -Force | Out-Null
    }
    $HtmlArgs = @("--date", $Date, "--out", $TemplatesDir)
    if (Test-Path $NBAGradedFile) { $HtmlArgs += @("--nba", $NBAGradedFile) }
    if (Test-Path $CBBGradedFile) { $HtmlArgs += @("--cbb", $CBBGradedFile) }
    if (Test-Path $NHLGradedFile) { $HtmlArgs += @("--nhl", $NHLGradedFile) }
    if (Test-Path $SoccerGradedFile) { $HtmlArgs += @("--soccer", $SoccerGradedFile) }
    if (Test-Path $MLBGradedFile) { $HtmlArgs += @("--mlb", $MLBGradedFile) }

    if (($HtmlArgs -contains "--nba") -or ($HtmlArgs -contains "--cbb") -or ($HtmlArgs -contains "--nhl") -or ($HtmlArgs -contains "--soccer") -or ($HtmlArgs -contains "--mlb")) {
        Run-Py "Build Grades HTML" $Root $BuildGradesHtmlScript $HtmlArgs
        # Keep mobile/www in sync with ui_runner/templates (Grades iframe uses same-dir slate_eval_*.html).
        if (Test-Path -LiteralPath $MobileWwwDir) {
            $seSrc = Join-Path $TemplatesDir "slate_eval_$Date.html"
            $gpSrc = Join-Path $TemplatesDir "graded_props_$Date.json"
            if (Test-Path $seSrc) {
                Copy-Item -LiteralPath $seSrc -Destination (Join-Path $MobileWwwDir "slate_eval_$Date.html") -Force -ErrorAction SilentlyContinue
                Write-Host "[GRADER] Mobile copy: slate_eval_$Date.html -> mobile\www\" -ForegroundColor DarkGray
            }
            if (Test-Path $gpSrc) {
                Copy-Item -LiteralPath $gpSrc -Destination (Join-Path $MobileWwwDir "graded_props_$Date.json") -Force -ErrorAction SilentlyContinue
                Write-Host "[GRADER] Mobile copy: graded_props_$Date.json -> mobile\www\" -ForegroundColor DarkGray
            }
        }
    }
    else {
        Write-Host "Skipping HTML build (no graded workbook found)." -ForegroundColor Yellow
    }
}
else {
    Write-Host "Skipping HTML build (build_grades_html.py not found)." -ForegroundColor Yellow
}

# =============================
# Write graded_props_<date>.json for Prop Evaluation cards
# =============================
if (Test-Path $BackfillGradedPropsJsonScript) {
    Run-Py "Build graded props JSON" $Root $BackfillGradedPropsJsonScript @(
        "--date", $Date
    )
}
else {
    Write-Host "Skipping graded props JSON build (backfill_graded_props_json.py not found)." -ForegroundColor Yellow
}

# =============================
# Ingest graded_props JSON -> proporacle_income.db (dashboard / ROI)
# =============================
if ((Test-Path $IngestGradedIncomeScript) -and (Test-Path (Join-Path $TemplatesDir "graded_props_$Date.json"))) {
    Run-Py "Ingest graded props to income DB" $Root $IngestGradedIncomeScript @(
        "--date", $Date
    )
}
elseif (-not (Test-Path $IngestGradedIncomeScript)) {
    Write-Host "Skipping income DB ingest (ingest_graded_to_income_db.py not found)." -ForegroundColor Yellow
}

# Publish graded_props JSON so Railway can serve /grades/props/<date> (set PROPORACLE_SKIP_GRADES_GIT_PUSH=1 to skip).
if ($env:PROPORACLE_SKIP_GRADES_GIT_PUSH -ne "1") {
    $GpJson = Join-Path $TemplatesDir "graded_props_$Date.json"
    if (Test-Path $GpJson) {
        Push-Location $Root
        try {
            $env:GIT_TERMINAL_PROMPT = "0"
            $gpRel = "ui_runner/templates/graded_props_$Date.json"
            git add -- $gpRel 2>$null | Out-Null
            git diff --cached --quiet
            if ($LASTEXITCODE -ne 0) {
                git commit -m "data: graded props $Date"
                if ($LASTEXITCODE -eq 0) {
                    git push origin HEAD 2>$null
                }
            }
        }
        catch {
            Write-Warning "Graded props git publish skipped: $_"
        }
        finally {
            Pop-Location
        }
    }
}

# =============================
# Backfill MyTicketPerformance entry_legs using cached historical actuals
# =============================
if (Test-Path $EntryLegGraderScript) {
    Run-Py "Backfill Entry Legs (DB)" $Root $EntryLegGraderScript @()
}
else {
    Write-Host "Skipping entry leg backfill (grade_entry_legs.py not found)." -ForegroundColor Yellow
}

# =============================
# Run Combined Ticket Grader
# =============================
if (-not (Test-Path $TicketsFile)) {
    Write-Host "Tickets file not found (no combined_slate_tickets source for $Date)" -ForegroundColor Yellow
}
elseif (-not (Test-Path $NBAActuals)) {
    Write-Host "NBA actuals not found: $NBAActuals" -ForegroundColor Yellow
}
elseif (-not (Test-Path $CombinedTicketGrader)) {
    Write-Host "Combined ticket grader script not found!" -ForegroundColor Red
}
else {
    $UiDataJson = Join-Path $Root "ui_runner\data\combined_slate_tickets_$Date.json"
    $TicketsArg = if (Test-Path $UiDataJson) { $UiDataJson } else { $TicketsFile }
    if ($TicketsArg -eq $UiDataJson) {
        Write-Host "[GRADER] Using ui_runner/data JSON tickets fast path: $UiDataJson" -ForegroundColor DarkGray
    }
    $GraderArgs = @(
        "--tickets", $TicketsArg,
        "--nba_actuals", $NBAActuals,
        "--out", (Join-Path $DateDir "combined_tickets_graded_$Date.xlsx")
    )
    if (Test-Path $CBBActuals) {
        $GraderArgs += @("--cbb_actuals", $CBBActuals)
    }
    else {
        Write-Host "CBB actuals not found (continuing without CBB): $CBBActuals" -ForegroundColor Yellow
    }
    if (Test-Path $NBA1HActuals) {
        $GraderArgs += @("--nba1h_actuals", $NBA1HActuals)
    }
    if (Test-Path $NBA1QActuals) {
        $GraderArgs += @("--nba1q_actuals", $NBA1QActuals)
    }
    if (Test-Path $NHLActuals) {
        $GraderArgs += @("--nhl_actuals", $NHLActuals)
    }
    if (Test-Path $SoccerActuals) {
        $GraderArgs += @("--soccer_actuals", $SoccerActuals)
    }
    if (Test-Path $TennisActuals) {
        $GraderArgs += @("--tennis_actuals", $TennisActuals)
    }
    if (Test-Path $MlbActuals) {
        $GraderArgs += @("--mlb_actuals", $MlbActuals)
    }
    $InjNBA = Join-Path $DateDir "injuries_nba_$Date.csv"
    $InjCBB = Join-Path $DateDir "injuries_cbb_$Date.csv"
    $InjNHL = Join-Path $DateDir "injuries_nhl_$Date.csv"
    $InjSoc = Join-Path $DateDir "injuries_soccer_$Date.csv"
    if (Test-Path $InjNBA) { $GraderArgs += @("--nba_injuries", $InjNBA) }
    if (Test-Path $InjCBB) { $GraderArgs += @("--cbb_injuries", $InjCBB) }
    if (Test-Path $InjNHL) { $GraderArgs += @("--nhl_injuries", $InjNHL) }
    if (Test-Path $InjSoc) { $GraderArgs += @("--soccer_injuries", $InjSoc) }
    Run-Py "Combined Ticket Grader" $Root $CombinedTicketGrader $GraderArgs
}

# =============================
# Build Ticket Eval HTML for Grades tab
# =============================
if (Test-Path $TicketEvalBuilderScript) {
    $GradedCombined = Join-Path $DateDir "combined_tickets_graded_$Date.xlsx"
    $TicketEvalOut = Join-Path $TemplatesDir "ticket_eval_$Date.html"
    if (Test-Path $GradedCombined) {
        $TeArgs = @(
            "--date", $Date,
            "--graded", $GradedCombined,
            "--out", $TicketEvalOut
        )
        # Optional: extra graded folders (comma-separated YYYY-MM-DD). Leg game_time dates are
        # auto-detected inside build_ticket_eval.py; use this when tickets lack game_time (e.g. old xlsx).
        if ($env:PROPORACLE_TICKET_EVAL_GAME_DATE -and $env:PROPORACLE_TICKET_EVAL_GAME_DATE.Trim()) {
            foreach ($gd in ($env:PROPORACLE_TICKET_EVAL_GAME_DATE -split ',')) {
                $t = $gd.Trim()
                if ($t -match '^\d{4}-\d{2}-\d{2}$') {
                    $TeArgs += @("--game-date", $t)
                }
            }
        }
        Run-Py "Build Ticket Eval HTML" $Root $TicketEvalBuilderScript $TeArgs
        if ((Test-Path -LiteralPath $MobileWwwDir) -and (Test-Path -LiteralPath $TicketEvalOut)) {
            Copy-Item -LiteralPath $TicketEvalOut -Destination (Join-Path $MobileWwwDir "ticket_eval_$Date.html") -Force -ErrorAction SilentlyContinue
            Write-Host "[GRADER] Mobile copy: ticket_eval_$Date.html -> mobile\www\" -ForegroundColor DarkGray
        }
        Write-Host "[GRADER] Ticket eval merges graded_* for slate date and each leg game_time date (see build_ticket_eval log)." -ForegroundColor DarkGray
    }
    else {
        Write-Host "Skipping ticket eval build (graded workbook missing: $GradedCombined)." -ForegroundColor Yellow
    }
}
else {
    Write-Host "Skipping ticket eval build (build_ticket_eval.py not found)." -ForegroundColor Yellow
}

# Publish ticket eval + slate eval HTML (after combined ticket grader + build above).
if ($env:PROPORACLE_SKIP_GRADES_GIT_PUSH -ne "1") {
    $TeHtml = Join-Path $TemplatesDir "ticket_eval_$Date.html"
    $SeHtml = Join-Path $TemplatesDir "slate_eval_$Date.html"
    if ((Test-Path $TeHtml) -or (Test-Path $SeHtml)) {
        Push-Location $Root
        try {
            $env:GIT_TERMINAL_PROMPT = "0"
            $teRel = "ui_runner/templates/ticket_eval_$Date.html"
            $seRel = "ui_runner/templates/slate_eval_$Date.html"
            if (Test-Path $TeHtml) { git add -- $teRel 2>$null | Out-Null }
            if (Test-Path $SeHtml) { git add -- $seRel 2>$null | Out-Null }
            git diff --cached --quiet
            if ($LASTEXITCODE -ne 0) {
                git commit -m "data: ticket eval + slate eval grades $Date"
                if ($LASTEXITCODE -eq 0) {
                    git push origin HEAD 2>$null
                    Write-Host "[GRADER] Grades ticket/slate HTML pushed for $Date" -ForegroundColor Green
                }
            }
        }
        catch {
            Write-Warning "Grades HTML git publish skipped: $_"
        }
        finally {
            Pop-Location
        }
    }
}

# =============================
# Copy graded workbooks for Railway / git (outputs/ is not deployed)
# =============================
if (Test-Path $DateDir) {
    Copy-PropOracleGradedSlateBundle -RepoRoot $Root -GradeDate $Date -OutputsDir $DateDir -MaxFileBytes $GradedSlateMaxBytes
    if (-not $PushGradedSlate) {
        Write-Host "[GRADER] To commit and push graded_slate, re-run with -PushGradedSlate" -ForegroundColor DarkGray
    }
    else {
        Push-Location $Root
        try {
            & git rev-parse --is-inside-work-tree 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[GRADER] PushGradedSlate skipped (not a git repository)." -ForegroundColor Yellow
            }
            else {
                $gsPath = "ui_runner/graded_slate/$Date"
                git add -- $gsPath
                git diff --cached --quiet
                if ($LASTEXITCODE -ne 0) {
                    git commit -m "data: graded slate $Date"
                    git push origin HEAD
                    Write-Host "[GRADER] Graded slate pushed for $Date" -ForegroundColor Green
                }
                else {
                    Write-Host "[GRADER] No graded_slate changes to commit." -ForegroundColor DarkGray
                }
            }
        }
        finally {
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "DONE." -ForegroundColor Green

# =============================
# Archive graded props to history DB (step_archive.py)
# =============================
$StepArchiveScript = Join-Path $Root "scripts\step_archive.py"
if (Test-Path $StepArchiveScript) {
    $ArchivePairs = @(
        @{ Sport = "NBA";    File = $NBAGradedFile },
        @{ Sport = "CBB";    File = $CBBGradedFile },
        @{ Sport = "WCBB";   File = $WCBBGradedFile },
        @{ Sport = "NBA1H";  File = $NBA1HGradedFile },
        @{ Sport = "NBA1Q";  File = $NBA1QGradedFile },
        @{ Sport = "NHL";    File = $NHLGradedFile },
        @{ Sport = "MLB";    File = $MLBGradedFile },
        @{ Sport = "Soccer"; File = $SoccerGradedFile }
    )
    foreach ($pair in $ArchivePairs) {
        if (Test-Path $pair.File) {
            Run-Py "Archive graded ($($pair.Sport))" $Root $StepArchiveScript @(
                "--sport", $pair.Sport,
                "--graded", $pair.File,
                "--date", $Date
            )
        }
    }
}
else {
    Write-Host "Skipping archive step (step_archive.py not found)." -ForegroundColor Yellow
}

# =============================
# Auto-retrain trigger (weekly)
# =============================
$TrainingLog = Join-Path $Root "models\\training_log.csv"
$ShouldRetrain = $false
if (-not (Test-Path $TrainingLog)) {
    $ShouldRetrain = $true
}
else {
    try {
        $last = (Import-Csv $TrainingLog | Select-Object -Last 1)
        if ($null -eq $last -or -not $last.timestamp) {
            $ShouldRetrain = $true
        }
        else {
            $lastTs = [datetime]$last.timestamp
            $days = ((Get-Date) - $lastTs).TotalDays
            if ($days -ge 7) { $ShouldRetrain = $true }
        }
    }
    catch {
        $ShouldRetrain = $true
    }
}

if ($ShouldRetrain) {
    Write-Host "`n[AUTO-RETRAIN] Triggered (7+ days since last training, or no log)." -ForegroundColor Cyan
    $trainScripts = Get-ChildItem -Path (Join-Path $Root "scripts") -Filter "train_prop_model_*.py" | Sort-Object Name
    foreach ($s in $trainScripts) {
        Write-Host "[AUTO-RETRAIN] Running $($s.Name)..." -ForegroundColor Cyan
        Run-Py "Auto-Retrain $($s.BaseName)" $Root $s.FullName @()
    }
    Write-Host "[AUTO-RETRAIN] Complete." -ForegroundColor Green
}
