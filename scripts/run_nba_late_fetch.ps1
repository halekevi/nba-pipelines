#requires -Version 5.1
<#
.SYNOPSIS
  Mid-day full slate refresh: re-fetch all sports with step1 --append, then full pipeline with -SkipFetch.
.NOTES
  Task Scheduler entry PropORACLE_NBA_LateFetch points here; filename kept for existing registrations.
  Per-sport step1 failures are non-fatal; pipeline failure exits 1.
#>
param(
    [switch]$NoOverwrite
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Host "[LATE_FETCH] Starting full slate re-fetch $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

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
        Write-Host "[LATE_FETCH][NO-OVERWRITE] Preserved '$Path' -> '$backup' ($Reason)" -ForegroundColor DarkGray
    }
    else {
        Write-Host "[LATE_FETCH][NO-OVERWRITE] Preserved '$Path' -> '$backup'" -ForegroundColor DarkGray
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

# NHL — append (semantic dedupe in script)
Write-Host "[LATE_FETCH] Fetching NHL props (append)..."
$NHLDir = Join-Path $Root "NHL"
Push-Location $NHLDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_nhl.py" "--append" "--output" "outputs\step1_nhl_props.csv"
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] NHL step1 failed — continuing" -ForegroundColor Yellow
}

# Soccer
Write-Host "[LATE_FETCH] Fetching Soccer props (append)..."
$SoccerDir = Join-Path $Root "Soccer"
Push-Location $SoccerDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_soccer.py" "--append" "--output" "outputs\step1_soccer_props.csv"
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] Soccer step1 failed — continuing" -ForegroundColor Yellow
}

# MLB
Write-Host "[LATE_FETCH] Fetching MLB props (append)..."
$MLBDir = Join-Path $Root "MLB"
Push-Location $MLBDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_mlb.py" "--append" "--output" "step1_mlb_props.csv"
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    $mlbOut = Join-Path $MLBDir "step1_mlb_props.csv"
    $mlbRows = Get-CsvDataRowCount -CsvPath $mlbOut
    if ($mlbRows -gt 0) {
        Write-Host "[LATE_FETCH] MLB step1 failed but fallback rows are present ($mlbRows) — continuing" -ForegroundColor Yellow
    }
    else {
        Write-Host "[LATE_FETCH][HIGH] MLB step1 failed and no fallback rows are available. Continuing pipeline for other sports." -ForegroundColor Red
    }
}

# NFL late fetch — activate week of Sep 7, 2026
# $NFLActive = (Get-Date) -ge [DateTime]"2026-09-07"
# if ($NFLActive) {
#     Write-Host "[LATE_FETCH] Fetching NFL props (append)..."
#     $NFLDir = Join-Path $Root "NFL"
#     Push-Location $NFLDir
#     try {
#         & py -3.14 ".\scripts\step1_fetch_prizepicks_nfl.py" "--append" "--output" "data\outputs\step1_pp_props_today.csv"
#     }
#     finally {
#         Pop-Location
#     }
#     if ($LASTEXITCODE -ne 0) {
#         Write-Host "[LATE_FETCH] NFL step1 failed — continuing" -ForegroundColor Yellow
#     }
# }

$pipeScript = Join-Path $Root "run_pipeline.ps1"
if (-not (Test-Path $pipeScript)) {
    Write-Host "[LATE_FETCH] Missing run_pipeline.ps1 at $pipeScript" -ForegroundColor Red
    exit 1
}

Write-Host "[LATE_FETCH] Running full pipeline -SkipFetch..."
if ($NoOverwrite) {
    $today = Get-Date -Format "yyyy-MM-dd"
    $preserveTargets = @(
        (Join-Path $Root "outputs\$today\combined_slate_tickets_$today.xlsx"),
        (Join-Path $Root "outputs\$today\combined_slate_tickets_$today.json"),
        (Join-Path $Root "ui_runner\templates\tickets_latest.html"),
        (Join-Path $Root "ui_runner\templates\tickets_latest.json"),
        (Join-Path $Root "ui_runner\templates\slate_latest.json"),
        (Join-Path $Root "ui_runner\templates\slate_eval_$today.html"),
        (Join-Path $Root "ui_runner\templates\ticket_eval_$today.html"),
        (Join-Path $Root "ui_runner\templates\graded_props_$today.json"),
        (Join-Path $Root "NBA\step8_all_direction_clean.xlsx"),
        (Join-Path $Root "Soccer\step8_soccer_direction_clean.xlsx"),
        (Join-Path $Root "MLB\step8_mlb_direction_clean.xlsx"),
        (Join-Path $Root "Tennis\step8_tennis_direction_clean.xlsx")
    )
    foreach ($pt in $preserveTargets) {
        Preserve-ExistingFile -Path $pt -Reason "pre-LATE_FETCH pipeline snapshot"
    }
}
& pwsh -NoProfile -File $pipeScript -SkipFetch
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] Pipeline failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

Write-Host "[LATE_FETCH] Done $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
exit 0
