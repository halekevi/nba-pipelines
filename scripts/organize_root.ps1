# ============================================================
#  organize_root.ps1
#  Moves loose files in PropOracle root into proper folders
#  Safe - only moves files, never deletes
#  Usage:
#    .\organize_root.ps1           # Preview
#    .\organize_root.ps1 -Execute  # Apply
# ============================================================
param([switch]$Execute)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition

if (-not $Execute) {
    Write-Host ""
    Write-Host "====================================================" -ForegroundColor Cyan
    Write-Host "  PREVIEW MODE - run with -Execute to apply" -ForegroundColor Cyan
    Write-Host "====================================================" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "====================================================" -ForegroundColor Yellow
    Write-Host "  EXECUTE MODE - moving files now..." -ForegroundColor Yellow
    Write-Host "====================================================" -ForegroundColor Yellow
}
Write-Host ""

function Move-File {
    param([string]$File, [string]$DestFolder)
    $src = "$Root\$File"
    $dst = "$Root\$DestFolder\$File"
    if (Test-Path $src) {
        Write-Host "  [MOVE] $File" -ForegroundColor Yellow
        Write-Host "         -> $DestFolder\" -ForegroundColor DarkGray
        if ($Execute) {
            try {
                if (-not (Test-Path "$Root\$DestFolder")) {
                    New-Item -ItemType Directory -Force -Path "$Root\$DestFolder" | Out-Null
                }
                Move-Item $src $dst -Force -ErrorAction Stop
                Write-Host "         OK" -ForegroundColor Green
            } catch {
                Write-Host "         FAILED: $_" -ForegroundColor Red
            }
        }
    }
}

# ── Pipeline scripts -> scripts\ ─────────────────────────────────────────────
Write-Host "[ 1 ] Pipeline scripts -> scripts\" -ForegroundColor Magenta
Write-Host ""
Move-File "combined_slate_tickets.py"     "scripts"
Move-File "combined_ticket_grader.py"     "scripts"
Move-File "fetch_actuals.py"              "scripts"
Move-File "extract_nba_slate.py"          "scripts"
Move-File "extract_cbb_slate.py"          "scripts"
Move-File "fetch_cbb_actuals_by_date.py"  "scripts"
Move-File "render_combined_slate_latest.py" "scripts"

# ── Grading scripts -> scripts\grading\ ──────────────────────────────────────
Write-Host ""
Write-Host "[ 2 ] Grading scripts -> scripts\grading\" -ForegroundColor Magenta
Write-Host ""
Move-File "slate_grader.py"        "scripts\grading"
Move-File "grade_cbb_full_slate.py" "scripts\grading"
Move-File "build_grades_html.py"   "scripts\grading"

# ── UI scripts -> scripts\ui\ ────────────────────────────────────────────────
Write-Host ""
Write-Host "[ 3 ] UI scripts -> scripts\ui\" -ForegroundColor Magenta
Write-Host ""
Move-File "build_tickets_html.py" "scripts\ui"

# ── JSX components -> ui_runner\components\ ──────────────────────────────────
Write-Host ""
Write-Host "[ 4 ] JSX files -> ui_runner\components\" -ForegroundColor Magenta
Write-Host ""
Move-File "payout_calculator.jsx"        "ui_runner\components"
Move-File "payout_calculator_render.jsx" "ui_runner\components"
Move-File "pipeline_dashboard.jsx"       "ui_runner\components"

# ── Dev/misc files -> archive\legacy\dev\ ────────────────────────────────────
Write-Host ""
Write-Host "[ 5 ] Dev files -> archive\legacy\dev\" -ForegroundColor Magenta
Write-Host ""
Move-File "GITHUB_DEPLOY.md"                      "archive\legacy\dev"
Move-File "powershell_cheatsheet.txt"             "archive\legacy\dev"
Move-File "prizepicks_payout_engine_progress.xlsx" "archive\legacy\dev"
Move-File "prizepicks_payout_engine_progress.md"  "archive\legacy\dev"

# ── Update run_pipeline.ps1 paths ────────────────────────────────────────────
Write-Host ""
Write-Host "[ 6 ] Update run_pipeline.ps1 script paths" -ForegroundColor Magenta
Write-Host ""

$pipelinePs1 = "$Root\run_pipeline.ps1"
if (Test-Path $pipelinePs1) {
    Write-Host "  [UPDATE] run_pipeline.ps1" -ForegroundColor Yellow
    if ($Execute) {
        $content = Get-Content $pipelinePs1 -Raw -Encoding UTF8
        $content = $content -replace '"\\"\\combined_slate_tickets\.py"', '".\scripts\combined_slate_tickets.py"'
        $content = $content -replace '"\.\\combined_slate_tickets\.py"', '".\scripts\combined_slate_tickets.py"'
        $content = $content -replace "combined_slate_tickets\.py", "scripts\combined_slate_tickets.py"
        Set-Content $pipelinePs1 -Value $content -Encoding UTF8
        Write-Host "  OK" -ForegroundColor Green
    }
}

# ── Update run_grader.ps1 paths ───────────────────────────────────────────────
$graderPs1 = "$Root\run_grader.ps1"
if (Test-Path $graderPs1) {
    Write-Host "  [UPDATE] run_grader.ps1" -ForegroundColor Yellow
    if ($Execute) {
        $content = Get-Content $graderPs1 -Raw -Encoding UTF8
        $content = $content -replace '"\.\\slate_grader\.py"',              '".\scripts\grading\slate_grader.py"'
        $content = $content -replace '"\.\\grade_cbb_full_slate\.py"',      '".\scripts\grading\grade_cbb_full_slate.py"'
        $content = $content -replace '"\.\\build_grades_html\.py"',         '".\scripts\grading\build_grades_html.py"'
        $content = $content -replace '"\.\\extract_nba_slate\.py"',         '".\scripts\extract_nba_slate.py"'
        $content = $content -replace '"\.\\extract_cbb_slate\.py"',         '".\scripts\extract_cbb_slate.py"'
        $content = $content -replace '"\.\\fetch_actuals\.py"',             '".\scripts\fetch_actuals.py"'
        $content = $content -replace '"\.\\combined_ticket_grader\.py"',    '".\scripts\combined_ticket_grader.py"'
        $content = $content -replace '"\.\\fetch_cbb_actuals_by_date\.py"', '".\scripts\fetch_cbb_actuals_by_date.py"'
        Set-Content $graderPs1 -Value $content -Encoding UTF8
        Write-Host "  OK" -ForegroundColor Green
    }
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
if (-not $Execute) {
    Write-Host "  PREVIEW DONE - run with -Execute to apply" -ForegroundColor Cyan
} else {
    Write-Host "  DONE. Root is now clean." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Root should now only contain:" -ForegroundColor White
    Write-Host "    run_pipeline.ps1" -ForegroundColor DarkGray
    Write-Host "    run_grader.ps1" -ForegroundColor DarkGray
    Write-Host "    cleanup_pipeline.ps1" -ForegroundColor DarkGray
    Write-Host "    organize_root.ps1" -ForegroundColor DarkGray
    Write-Host "    organize_folder.ps1" -ForegroundColor DarkGray
    Write-Host "    git_push_log.txt" -ForegroundColor DarkGray
    Write-Host "    .gitignore / .gitattributes" -ForegroundColor DarkGray
    Write-Host "    combined_slate_tickets_TODAY.xlsx" -ForegroundColor DarkGray
    Write-Host "    scripts\ / NBA\ / CBB\" -ForegroundColor DarkGray
    Write-Host "    outputs\ / ui_runner\ / archive\ / grades\" -ForegroundColor DarkGray
}
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

