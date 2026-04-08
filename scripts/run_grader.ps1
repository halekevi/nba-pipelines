param(
    [string]$Date = ((Get-Date).AddDays(-1).ToString("yyyy-MM-dd"))
)

$Root = Split-Path $PSScriptRoot -Parent
$DateDir = Join-Path $Root "outputs\$Date"

$TicketsFileXlsx = Join-Path $DateDir "combined_slate_tickets_$Date.xlsx"
$TicketsFileJson = Join-Path $DateDir "combined_slate_tickets_$Date.json"
$TicketsFile = if (Test-Path $TicketsFileXlsx) { $TicketsFileXlsx } elseif (Test-Path $TicketsFileJson) { $TicketsFileJson } else { $TicketsFileXlsx }
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
$FetchActualsScript = Join-Path $Root "scripts\fetch_actuals.py"
$FetchNBAPeriodActualsScript = Join-Path $Root "scripts\fetch_nba_period_actuals.py"
$BuildNBA1QHistoryScript = Join-Path $Root "scripts\build_nba1q_history_db.py"
$SlateGraderScript = Join-Path $Root "scripts\grading\slate_grader.py"
$CBBFullGraderScript = Join-Path $Root "scripts\grading\grade_cbb_full_slate.py"
$NHLAdvancedGraderScript = Join-Path $Root "scripts\nhl_grader_advanced.py"
$SoccerAdvancedGraderScript = Join-Path $Root "scripts\soccer_grader_advanced.py"
$BuildGradesHtmlScript = Join-Path $Root "scripts\grading\build_grades_html.py"
$BackfillGradedPropsJsonScript = Join-Path $Root "scripts\backfill_graded_props_json.py"
$NBABacktestScript = Join-Path $Root "NBA\scripts\backtest_nba.py"
$TicketEvalBuilderScript = Join-Path $Root "scripts\build_ticket_eval_html.py"
if (-not (Test-Path $TicketEvalBuilderScript)) {
    $TicketEvalBuilderScript = Join-Path $Root "scripts\build_ticket_eval.py"
}
$EntryLegGraderScript = Join-Path $Root "scripts\grade_entry_legs.py"

$NBAGradedFile = Join-Path $DateDir "graded_nba_$Date.xlsx"
$CBBGradedFile = Join-Path $DateDir "graded_cbb_$Date.xlsx"
$NHLGradedFile = Join-Path $DateDir "graded_nhl_$Date.xlsx"
$SoccerGradedFile = Join-Path $DateDir "graded_soccer_$Date.xlsx"
$NBA1HGradedFile = Join-Path $DateDir "graded_nba1h_$Date.xlsx"
$NBA1QGradedFile = Join-Path $DateDir "graded_nba1q_$Date.xlsx"
$WCBBGradedFile = Join-Path $DateDir "graded_wcbb_$Date.xlsx"
$EvalHtmlFile = Join-Path $DateDir "slate_eval_$Date.html"
$TemplatesDir = Join-Path $Root "ui_runner\templates"

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
        "--output", $NBAActuals
    )

    Run-Py "Fetch CBB Actuals" $Root $FetchActualsScript @(
        "--sport", "CBB",
        "--date", $Date,
        "--output", $CBBActuals,
        "--window", "0"
    )

    Run-Py "Fetch WCBB Actuals" $Root $FetchActualsScript @(
        "--sport", "WCBB",
        "--date", $Date,
        "--output", $WCBBActuals,
        "--window", "0"
    )

    Run-Py "Fetch NHL Actuals" $Root $FetchActualsScript @(
        "--sport", "NHL",
        "--date", $Date,
        "--output", $NHLActuals
    )

    Run-Py "Fetch Soccer Actuals" $Root $FetchActualsScript @(
        "--sport", "Soccer",
        "--date", $Date,
        "--output", $SoccerActuals
    )

    if (Test-Path $FetchNBAPeriodActualsScript) {
        Run-Py "Fetch NBA 1H Actuals" $Root $FetchNBAPeriodActualsScript @(
            "--date", $Date,
            "--segment", "1H",
            "--output", $NBA1HActuals
        )
        Run-Py "Fetch NBA 1Q Actuals" $Root $FetchNBAPeriodActualsScript @(
            "--date", $Date,
            "--segment", "1Q",
            "--output", $NBA1QActuals
        )
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
        Run-Py "Fetch CBB 1H Actuals (ESPN PBP)" $Root $FetchNBAPeriodActualsScript @(
            "--sport", "CBB",
            "--date", $Date,
            "--segment", "1H",
            "--output", $CBB1HActuals
        )
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
$NBASlateFile = Resolve-FirstExisting @(
    (Join-Path $DateDir "nba_slate_extracted_$Date.xlsx"),
    (Join-Path $DateDir "step8_nba_direction_clean_$Date.xlsx"),
    (Join-Path $Root "NBA\data\outputs\step8_all_direction_clean.xlsx"),
    (Join-Path $Root "NBA\step8_all_direction_clean.xlsx")
)
$CBBSlateXlsx = Resolve-FirstExisting @(
    (Join-Path $DateDir "step6_ranked_cbb_$Date.xlsx"),
    (Join-Path $Root "CBB\step6_ranked_cbb.xlsx")
)
$CBBSlateCsv = Join-Path $DateDir "cbb_slate_extracted_$Date.csv"
$NHLSlateFile = Resolve-FirstExisting @(
    (Join-Path $DateDir "step8_nhl_direction_clean_$Date.xlsx"),
    (Join-Path $Root "NHL\outputs\step8_nhl_direction_clean.xlsx"),
    (Join-Path $Root "NHL\step8_nhl_direction_clean.xlsx")
)
$SoccerSlateFile = Resolve-FirstExisting @(
    (Join-Path $DateDir "step8_soccer_direction_clean_$Date.xlsx"),
    (Join-Path $Root "Soccer\outputs\step8_soccer_direction_clean.xlsx"),
    (Join-Path $Root "Soccer\step8_soccer_direction_clean.xlsx")
)

# Build dated NBA1H/1Q slates from root workbook when archive missing (filters by Game Time == $Date).
$ExtractNbaSlateScript = Join-Path $Root "scripts\extract_nba_slate_for_grade_date.py"
$DatedNBA1HPath = Join-Path $DateDir "step8_nba1h_direction_clean_$Date.xlsx"
$DatedNBA1QPath = Join-Path $DateDir "step8_nba1q_direction_clean_$Date.xlsx"
$RootNBA1HPath = Join-Path $Root "NBA\step8_nba1h_direction_clean.xlsx"
$RootNBA1QPath = Join-Path $Root "NBA\step8_nba1q_direction_clean.xlsx"
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
    (Join-Path $Root "NBA\step8_nba1h_direction_clean.xlsx")
)
$NBA1QSlateFile = Resolve-FirstExisting @(
    $DatedNBA1QPath,
    (Join-Path $Root "NBA\step8_nba1q_direction_clean.xlsx")
)
$WCBBSlateFile = Resolve-FirstExisting @(
    (Join-Path $DateDir "step6_ranked_wcbb_$Date.xlsx"),
    (Join-Path $Root "CBB\step6_ranked_wcbb.xlsx")
)
if ($SoccerSlateFile -and (Test-Path $SoccerSlateFile)) {
    Write-Host "Soccer slate resolved to: $SoccerSlateFile" -ForegroundColor DarkGray
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
            "--out-dir", (Join-Path $Root "NBA\data\outputs")
        )

        Run-Py "Backtest NBA (All Historical Actuals)" $Root $NBABacktestScript @(
            "--slate", $NBASlateFile,
            "--out-dir", (Join-Path $Root "NBA\data\outputs"),
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

if ((Test-Path $CBBActuals) -and (Test-Path $CBBSlateCsv) -and (Test-Path $CBBFullGraderScript)) {
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
    $MLBSlateFile = Resolve-FirstExisting @(
        (Join-Path $DateDir "step8_mlb_direction_clean_$Date.xlsx"),
        (Join-Path $Root "MLB\outputs\step8_mlb_direction_clean.xlsx"),
        (Join-Path $Root "MLB\step8_mlb_direction_clean.xlsx")
    )
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

if ($NBA1HSlateFile -and (Test-Path $NBA1HSlateFile) -and (Test-Path $SlateGraderScript) -and ((Test-Path $NBA1HActuals) -or (Test-Path $NBAActuals))) {
    $NBA1HActualsForGrade = if (Test-Path $NBA1HActuals) { $NBA1HActuals } else { $NBAActuals }
    Run-Py "Grade NBA1H Slate" $Root $SlateGraderScript @(
        "--sport", "NBA",
        "--slate", $NBA1HSlateFile,
        "--actuals", $NBA1HActualsForGrade,
        "--output", $NBA1HGradedFile,
        "--date", $Date
    )
}
else {
    Write-Host "Skipping NBA1H grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

if ($NBA1QSlateFile -and (Test-Path $NBA1QSlateFile) -and (Test-Path $SlateGraderScript) -and ((Test-Path $NBA1QActuals) -or (Test-Path $NBAActuals))) {
    $NBA1QActualsForGrade = if (Test-Path $NBA1QActuals) { $NBA1QActuals } else { $NBAActuals }
    Run-Py "Grade NBA1Q Slate" $Root $SlateGraderScript @(
        "--sport", "NBA",
        "--slate", $NBA1QSlateFile,
        "--actuals", $NBA1QActualsForGrade,
        "--output", $NBA1QGradedFile,
        "--date", $Date
    )
}
else {
    Write-Host "Skipping NBA1Q grading (missing slate/actuals/grader)." -ForegroundColor Yellow
}

if ($WCBBSlateFile -and (Test-Path $WCBBSlateFile) -and (Test-Path $SlateGraderScript)) {
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
        "--date", $Date,
        "--out", $TemplatesDir
    )
}
else {
    Write-Host "Skipping graded props JSON build (backfill_graded_props_json.py not found)." -ForegroundColor Yellow
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
if (-not (Test-Path $TicketsFileXlsx) -and -not (Test-Path $TicketsFileJson)) {
    Write-Host "Tickets file not found (no combined_slate_tickets .xlsx or .json for $Date)" -ForegroundColor Yellow
}
elseif (-not (Test-Path $NBAActuals)) {
    Write-Host "NBA actuals not found: $NBAActuals" -ForegroundColor Yellow
}
elseif (-not (Test-Path $CBBActuals)) {
    Write-Host "CBB actuals not found: $CBBActuals" -ForegroundColor Yellow
}
elseif (-not (Test-Path $CombinedTicketGrader)) {
    Write-Host "Combined ticket grader script not found!" -ForegroundColor Red
}
else {
    $GraderArgs = @(
        "--tickets", $TicketsFile,
        "--nba_actuals", $NBAActuals,
        "--cbb_actuals", $CBBActuals,
        "--out", (Join-Path $DateDir "combined_tickets_graded_$Date.xlsx")
    )
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
    Run-Py "Build Ticket Eval HTML" $Root $TicketEvalBuilderScript @(
        "--date", $Date
    )
}
else {
    Write-Host "Skipping ticket eval build (build_ticket_eval.py not found)." -ForegroundColor Yellow
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
