#requires -Version 5.1
<#
.SYNOPSIS
  Mid-day full slate refresh: re-fetch all sports with step1 --append, then full pipeline with -SkipFetch.
.NOTES
  Task Scheduler entry PropORACLE_NBA_LateFetch points here; filename kept for existing registrations.
  Writes step1 CSVs under outputs\<date>\<sport>\ (same paths as run_pipeline.ps1 -SkipFetch).
  Per-sport step1 failures are non-fatal; pipeline failure exits 1.
#>
param(
    [switch]$NoOverwrite
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$SportsRoot = Join-Path $Root "Sports"
Set-Location $Root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Resolve-PipelineSlateDate {
    $pipeDate = (Get-Date).ToString("yyyy-MM-dd")
    try {
        $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
        $etNow = [System.TimeZoneInfo]::ConvertTimeFromUtc((Get-Date).ToUniversalTime(), $tz)
        if ($etNow.Hour -ge 20) {
            $pipeDate = $etNow.Date.AddDays(1).ToString("yyyy-MM-dd")
        }
    } catch { }
    return $pipeDate
}

function Ensure-RunOutDir {
    param([string]$SportTag)
    $dir = Join-Path $Root "outputs\$PipeDate\$SportTag"
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    return $dir
}

function Copy-Step1Mirror {
    param([string]$Source, [string]$MirrorPath)
    if (-not (Test-Path -LiteralPath $Source)) { return }
    $mirrorDir = Split-Path -Parent $MirrorPath
    if (-not (Test-Path -LiteralPath $mirrorDir)) {
        New-Item -ItemType Directory -Force -Path $mirrorDir | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $MirrorPath -Force
}

Write-Host "[LATE_FETCH] Starting full slate re-fetch $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

$PipeDate = Resolve-PipelineSlateDate
Write-Host "[LATE_FETCH] Pipeline slate date: $PipeDate" -ForegroundColor Cyan

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

# NBA — append; dated output + legacy mirror
Write-Host "[LATE_FETCH] Fetching NBA props (append)..."
$NBADir = Join-Path $SportsRoot "NBA"
$nbaRunOut = Ensure-RunOutDir -SportTag "nba"
$nbaStep1 = Join-Path $nbaRunOut "step1_pp_props_today.csv"
$nbaLegacy = Join-Path $NBADir "data\outputs\step1_pp_props_today.csv"
$nbaArgs = @(
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
    "--date", $PipeDate,
    "--allow-nearest-future",
    "--output", $nbaStep1
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
elseif ((Get-CsvDataRowCount -CsvPath $nbaStep1) -gt 0) {
    Copy-Step1Mirror -Source $nbaStep1 -MirrorPath $nbaLegacy
}

# WNBA — full step1 fetch into dated folder (pipeline -SkipFetch reads this path)
Write-Host "[LATE_FETCH] Fetching WNBA props..."
$wnbaPs1 = Join-Path $Root "scripts\run_wnba_pipeline.ps1"
if (Test-Path -LiteralPath $wnbaPs1) {
    & pwsh -NoProfile -File $wnbaPs1 -Date $PipeDate -Step1Only
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[LATE_FETCH] WNBA step1 failed — continuing" -ForegroundColor Yellow
    }
}
else {
    Write-Host "[LATE_FETCH] WARN: missing $wnbaPs1 — skipping WNBA fetch" -ForegroundColor Yellow
}

# NHL — append
Write-Host "[LATE_FETCH] Fetching NHL props (append)..."
$NHLDir = Join-Path $SportsRoot "NHL"
$nhlRunOut = Ensure-RunOutDir -SportTag "nhl"
$nhlStep1 = Join-Path $nhlRunOut "step1_nhl_props.csv"
Push-Location $NHLDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_nhl.py" "--append" "--output" $nhlStep1
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] NHL step1 failed — continuing" -ForegroundColor Yellow
}
elseif ((Get-CsvDataRowCount -CsvPath $nhlStep1) -gt 0) {
    Copy-Step1Mirror -Source $nhlStep1 -MirrorPath (Join-Path $NHLDir "outputs\step1_nhl_props.csv")
}

# Soccer
Write-Host "[LATE_FETCH] Fetching Soccer props (append)..."
$SoccerDir = Join-Path $SportsRoot "Soccer"
$soccerRunOut = Ensure-RunOutDir -SportTag "soccer"
$soccerStep1 = Join-Path $soccerRunOut "step1_soccer_props.csv"
Push-Location $SoccerDir
try {
    & py -3.14 ".\scripts\step1_fetch_prizepicks_soccer.py" "--append" "--date" "$PipeDate" "--output" $soccerStep1
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] Soccer step1 failed — continuing" -ForegroundColor Yellow
}
elseif ((Get-CsvDataRowCount -CsvPath $soccerStep1) -gt 0) {
    Copy-Step1Mirror -Source $soccerStep1 -MirrorPath (Join-Path $SoccerDir "outputs\step1_soccer_props.csv")
}

# MLB - direct API first (dated output), then Playwright fallback
Write-Host "[LATE_FETCH] Fetching MLB props (append; direct API then Playwright if needed)..." -ForegroundColor Cyan
$MLBDir = Join-Path $SportsRoot "MLB"
$mlbRunOut = Ensure-RunOutDir -SportTag "mlb"
$mlbStep1 = Join-Path $mlbRunOut "step1_mlb_props.csv"
Push-Location $MLBDir
try {
    & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
        "--max-pages" "5" `
        "--api-retries" "5" `
        "--api-session-waves" "3" `
        "--api-wave-gap-min" "30" `
        "--api-wave-gap-max" "75" `
        "--api-403-cooldown-after" "4" `
        "--api-403-cooldown-seconds" "180" `
        "--api-403-cooldown-jitter-min" "20" `
        "--api-403-cooldown-jitter-max" "80" `
        "--append" `
        "--date" "$PipeDate" `
        "--output" $mlbStep1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[LATE_FETCH] MLB direct API step1 failed (exit $LASTEXITCODE) - trying Playwright" -ForegroundColor Yellow
        & py -3.14 -u ".\scripts\step1_fetch_prizepicks_mlb.py" `
            "--playwright" `
            "--timeout" "240" `
            "--append" `
            "--date" "$PipeDate" `
            "--output" $mlbStep1
    }
}
finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    $mlbRows = Get-CsvDataRowCount -CsvPath $mlbStep1
    if ($mlbRows -gt 0) {
        Write-Host "[LATE_FETCH] MLB step1 failed but fallback rows are present ($mlbRows) - continuing" -ForegroundColor Yellow
    }
    else {
        Write-Host "[LATE_FETCH][HIGH] MLB step1 failed and no fallback rows are available. Continuing pipeline for other sports." -ForegroundColor Red
    }
}
elseif ((Get-CsvDataRowCount -CsvPath $mlbStep1) -gt 0) {
    Copy-Step1Mirror -Source $mlbStep1 -MirrorPath (Join-Path $MLBDir "data\outputs\step1_mlb_props.csv")
}

$pipeScript = Join-Path $Root "run_pipeline.ps1"
if (-not (Test-Path $pipeScript)) {
    Write-Host "[LATE_FETCH] Missing run_pipeline.ps1 at $pipeScript" -ForegroundColor Red
    exit 1
}

Write-Host "[LATE_FETCH] Running full pipeline -SkipFetch -Date $PipeDate..."
if ($NoOverwrite) {
    $preserveTargets = @(
        (Join-Path $Root "outputs\$PipeDate\combined_slate_tickets_$PipeDate.xlsx"),
        (Join-Path $Root "outputs\$PipeDate\combined_slate_tickets_$PipeDate.json"),
        (Join-Path $Root "ui_runner\templates\tickets_latest.html"),
        (Join-Path $Root "ui_runner\templates\tickets_latest.json"),
        (Join-Path $Root "ui_runner\templates\slate_latest.json"),
        (Join-Path $Root "ui_runner\templates\slate_eval_$PipeDate.html"),
        (Join-Path $Root "ui_runner\templates\ticket_eval_$PipeDate.html"),
        (Join-Path $Root "ui_runner\templates\graded_props_$PipeDate.json"),
        (Join-Path $Root "Sports\NBA\step8_all_direction_clean.xlsx"),
        (Join-Path $Root "Sports\Soccer\step8_soccer_direction_clean.xlsx"),
        (Join-Path $Root "Sports\MLB\data\outputs\step8_mlb_direction_clean.xlsx"),
        (Join-Path $Root "Sports\MLB\step8_mlb_direction_clean.xlsx"),
        (Join-Path $Root "Sports\Tennis\step8_tennis_direction_clean.xlsx")
    )
    foreach ($pt in $preserveTargets) {
        Preserve-ExistingFile -Path $pt -Reason "pre-LATE_FETCH pipeline snapshot"
    }
}
& pwsh -NoProfile -File $pipeScript -SkipFetch -Date $PipeDate
if ($LASTEXITCODE -ne 0) {
    Write-Host "[LATE_FETCH] Pipeline failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

Write-Host "[LATE_FETCH] Done $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
exit 0
