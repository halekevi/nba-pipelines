param(
    [string]$Date = ((Get-Date).AddDays(-1).ToString("yyyy-MM-dd"))
)
# NBA 1H/1Q: use scripts\run_grader.ps1 (period actuals fetch + graded_nba1h/1q outputs).

$Root = Split-Path $PSScriptRoot -Parent
$DateDir = Join-Path $Root "outputs\$Date"

$TicketsFile = Join-Path $DateDir "combined_slate_tickets_$Date.xlsx"
$NBAActuals  = Join-Path $DateDir "actuals_nba_$Date.csv"
$CBBActuals  = Join-Path $DateDir "actuals_cbb_$Date.csv"
$NHLActuals  = Join-Path $DateDir "actuals_nhl_$Date.csv"
$SoccerActuals  = Join-Path $DateDir "actuals_soccer_$Date.csv"
$FetchActualsScript = Join-Path $Root "scripts\fetch_actuals.py"
$SlateGraderScript = Join-Path $Root "scripts\grading\slate_grader.py"
$CBBFullGraderScript = Join-Path $Root "scripts\grading\grade_cbb_full_slate.py"
$NHLAdvancedGraderScript = Join-Path $Root "scripts\nhl_grader_advanced.py"
$SoccerAdvancedGraderScript = Join-Path $Root "scripts\soccer_grader_advanced.py"
$BuildGradesHtmlScript = Join-Path $Root "scripts\grading\build_grades_html.py"

$NBASlateFile = Join-Path $Root "NBA\step8_all_direction_clean.xlsx"
$NBASlateFileDateStep8 = Join-Path $DateDir "step8_nba_direction_clean_$Date.xlsx"
$NBASlateFileExtracted = Join-Path $DateDir "nba_slate_extracted_$Date.xlsx"
$CBBSlateXlsx = Join-Path $Root "CBB\step6_ranked_cbb.xlsx"
$CBBSlateCsv = Join-Path $DateDir "cbb_slate_extracted_$Date.csv"
$NHLSlateFileDateStep8 = Join-Path $DateDir "step8_nhl_direction_clean_$Date.xlsx"
$NHLSlateFileRoot = Join-Path $Root "NHL\step8_nhl_direction_clean.xlsx"
$SoccerSlateFileDateStep8 = Join-Path $DateDir "step8_soccer_direction_clean_$Date.xlsx"
$SoccerSlateFileRoot = Join-Path $Root "Soccer\step8_soccer_direction_clean.xlsx"

$NBAGradedFile = Join-Path $DateDir "graded_nba_$Date.xlsx"
$CBBGradedFile = Join-Path $DateDir "graded_cbb_$Date.xlsx"
$NHLGradedFile = Join-Path $DateDir "graded_nhl_$Date.xlsx"
$SoccerGradedFile = Join-Path $DateDir "graded_soccer_$Date.xlsx"
$EvalHtmlFile = Join-Path $DateDir "slate_eval_$Date.html"
$TemplatesDir = Join-Path $Root "ui_runner\templates"

function Run-Py {
    param (
        [string]$Name,
        [string]$WorkingDir,
        [string]$ScriptPath,
        [string[]]$ScriptArgs
    )

    Write-Host "`n=== Running $Name ===" -ForegroundColor Cyan

    if (-not (Test-Path $ScriptPath)) {
        Write-Host "  Script not found: $ScriptPath" -ForegroundColor Yellow
        return
    }

    Push-Location $WorkingDir

    try {
        # Use python first to avoid py launcher version prompts.
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $env:PYTHONUTF8 = "1"
            $env:PYTHONIOENCODING = "utf-8"
            $pyArgs = @($ScriptPath) + $ScriptArgs
            & python -X utf8 @pyArgs
        }
        elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $env:PYTHONUTF8 = "1"
            $env:PYTHONIOENCODING = "utf-8"
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
}
else {
    Write-Host "Fetch actuals script not found: $FetchActualsScript" -ForegroundColor Yellow
}

# =============================
# Grade NBA/CBB + Build HTML
# =============================
if (Test-Path $NBASlateFileExtracted) {
    $NBASlateFile = $NBASlateFileExtracted
}
elseif (Test-Path $NBASlateFileDateStep8) {
    $NBASlateFile = $NBASlateFileDateStep8
}

if ((Test-Path $NBAActuals) -and (Test-Path $NBASlateFile) -and (Test-Path $SlateGraderScript)) {
    Run-Py "Grade NBA Slate" $Root $SlateGraderScript @(
        "--sport", "NBA",
        "--slate", $NBASlateFile,
        "--actuals", $NBAActuals,
        "--output", $NBAGradedFile,
        "--date", $Date
    )
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

if (Test-Path $NHLSlateFileDateStep8) {
    $NHLSlateFile = $NHLSlateFileDateStep8
}
else {
    $NHLSlateFile = $NHLSlateFileRoot
}

if ((Test-Path $NHLActuals) -and (Test-Path $NHLSlateFile) -and (Test-Path $NHLAdvancedGraderScript)) {
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

if (Test-Path $SoccerSlateFileDateStep8) {
    $SoccerSlateFile = $SoccerSlateFileDateStep8
}
else {
    $SoccerSlateFile = $SoccerSlateFileRoot
}

if ((Test-Path $SoccerActuals) -and (Test-Path $SoccerSlateFile) -and (Test-Path $SoccerAdvancedGraderScript)) {
    Run-Py "Grade Soccer Slate" $Root $SoccerAdvancedGraderScript @(
        "--date", $Date,
        "--actuals", $SoccerActuals,
        "--slate", $SoccerSlateFile,
        "--output-dir", $DateDir
    )
}
else {
    Write-Host "Skipping Soccer grading (missing slate/actuals/grader)." -ForegroundColor Yellow
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

    if (($HtmlArgs -contains "--nba") -or ($HtmlArgs -contains "--cbb") -or ($HtmlArgs -contains "--nhl") -or ($HtmlArgs -contains "--soccer")) {
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
# Run Combined Ticket Grader
# =============================
if (-not (Test-Path $TicketsFile)) {
    Write-Host "Tickets file not found: $TicketsFile" -ForegroundColor Yellow
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
    Run-Py "Combined Ticket Grader" $Root $CombinedTicketGrader @(
        "--tickets", $TicketsFile,
        "--nba_actuals", $NBAActuals,
        "--cbb_actuals", $CBBActuals,
        "--out", (Join-Path $DateDir "combined_tickets_graded_$Date.xlsx")
    )
}

$CombinedGradedOut = Join-Path $DateDir "combined_tickets_graded_$Date.xlsx"
$TicketEvalScript  = Join-Path $Root "scripts\build_ticket_eval_html.py"
$TicketEvalHtml    = Join-Path $TemplatesDir "ticket_eval_$Date.html"
if ((Test-Path $CombinedGradedOut) -and (Test-Path $TicketEvalScript)) {
    Run-Py "Build Ticket Eval HTML" $Root $TicketEvalScript @(
        "--date", $Date,
        "--graded", $CombinedGradedOut,
        "--out", $TicketEvalHtml
    )
}

Write-Host "`n✅ DONE." -ForegroundColor Green


