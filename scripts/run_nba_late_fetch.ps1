#requires -Version 5.1
<#
.SYNOPSIS
  Mid-day full slate refresh: re-fetch NBA (append), NHL/Soccer/MLB (overwrite), then full pipeline with -SkipFetch.
.NOTES
  Task Scheduler entry PropORACLE_NBA_LateFetch points here; filename kept for existing registrations.
  Per-sport step1 failures are non-fatal; pipeline failure exits 1.
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Host "[LATE_FETCH] Starting full slate re-fetch $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# NBA — append so early fetch rows are preserved when the board fills in
Write-Host "[LATE_FETCH] Fetching NBA props (append)..."
$NBADir = Join-Path $Root "NBA"
$nbaArgs = @(
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
    & py -3.14 ".\scripts\step1_fetch_prizepicks_api.py" @nbaArgs
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] NBA step1 failed — continuing other sports" -ForegroundColor Yellow
}

# NHL — standard overwrite (default script behavior)
Write-Host "[LATE_FETCH] Fetching NHL props..."
$NHLDir = Join-Path $Root "NHL"
Push-Location $NHLDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_nhl.py" "--output" "outputs\step1_nhl_props.csv"
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] NHL step1 failed — continuing" -ForegroundColor Yellow
}

# Soccer
Write-Host "[LATE_FETCH] Fetching Soccer props..."
$SoccerDir = Join-Path $Root "Soccer"
Push-Location $SoccerDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_soccer.py" "--output" "outputs\step1_soccer_props.csv"
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] Soccer step1 failed — continuing" -ForegroundColor Yellow
}

# MLB
Write-Host "[LATE_FETCH] Fetching MLB props..."
$MLBDir = Join-Path $Root "MLB"
Push-Location $MLBDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_mlb.py" "--output" "step1_mlb_props.csv"
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] MLB step1 failed — continuing" -ForegroundColor Yellow
}

$pipeScript = Join-Path $Root "run_pipeline.ps1"
if (-not (Test-Path $pipeScript)) {
    Write-Host "[LATE_FETCH] Missing run_pipeline.ps1 at $pipeScript" -ForegroundColor Red
    exit 1
}

Write-Host "[LATE_FETCH] Running full pipeline -SkipFetch..."
& pwsh -NoProfile -File $pipeScript -SkipFetch
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] Pipeline failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

Write-Host "[LATE_FETCH] Done $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
exit 0
