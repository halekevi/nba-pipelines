# ============================================================
#  PROP PIPELINE  -  Master Run Script  [MULTI-SPORT]
#
#  Usage:
#    .\run_pipeline.ps1                        # All sports parallel + Combined
#    .\run_pipeline.ps1 -NBAOnly               # NBA only + Combined
#    .\run_pipeline.ps1 -CBBOnly               # CBB only + Combined
#    .\run_pipeline.ps1 -NHLOnly               # NHL only + Combined
#    .\run_pipeline.ps1 -MLBOnly               # MLB only + Combined
#    .\run_pipeline.ps1 -SoccerOnly            # Soccer only + Combined
#    .\run_pipeline.ps1 -TennisOnly             # Tennis only (PrizePicks league_id=5) + Combined
#    .\run_pipeline.ps1 -WNBAOnly              # WNBA only (season-gated)
#    .\run_pipeline.ps1 -CombinedOnly          # Re-run combined using all existing outputs
#    .\run_pipeline.ps1 -SkipFetch             # Skip step1 fetch for whatever sport(s) run
#    .\run_pipeline.ps1 -NBAOnly -SkipFetch    # NBA steps 2-8 + Combined
#    .\run_pipeline.ps1 -NHLOnly -SkipFetch    # NHL steps 2-8 + Combined
#    .\run_pipeline.ps1 -SoccerOnly -SkipFetch # Soccer steps 2-8 + Combined
#    .\run_pipeline.ps1 -TennisOnly -SkipFetch # Tennis steps 2-8 + Combined
#    .\run_pipeline.ps1 -RefreshCache          # Wipe + rebuild ESPN cache before NBA
#    .\run_pipeline.ps1 -CacheAgeDays 7        # Auto-wipe cache if older than N days
#    .\run_pipeline.ps1 -SkipDailyGrader       # Skip run_grader + grade HTML git push after combined
#
#  Combined always auto-includes every sport whose step8 output exists on disk.
#  No -Include flags needed -- just run any sport, combined picks it up.
#
#  Prefer .\run_pipeline.ps1 from repo root; this copy under scripts\ is equivalent.
#  After combined + git push, runs scripts\run_grader.ps1 for (pipeline date - 1 day)
#  and pushes slate_eval_*.html / ticket_eval_*.html for that slate date.
# ============================================================
param(
    [string]$Date       = "",
    [string]$OddsApiKey = "10b3aa326aaec16be06e0fd074ed4ed9",
    [switch]$NBAOnly,
    [switch]$CBBOnly,
    [switch]$NHLOnly,
    [switch]$MLBOnly,
    [switch]$SoccerOnly,
    [switch]$TennisOnly,
    [switch]$WNBAOnly,
    [switch]$CombinedOnly,
    [switch]$SkipFetch,
    [switch]$RefreshCache,
    [switch]$ForceAll,
    [switch]$SkipDailyGrader,
    [int]$CacheAgeDays = 7
)

$ErrorActionPreference = "Continue"

# -- Date ---------------------------------------------------------------------
# US Eastern calendar date when omitted (matches combined_slate / Flask slate APIs;
# avoids "tomorrow" folder names when Railway or a UTC clock crosses midnight before US evening).
if (-not $Date) {
    try {
        $tzEt = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
        $Date = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tzEt).ToString("yyyy-MM-dd")
    } catch {
        $Date = Get-Date -Format "yyyy-MM-dd"
    }
    Write-Host "  [Date] No date specified, using US Eastern calendar date: $Date" -ForegroundColor DarkGray
} else {
    if ($Date -match "^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{4}$|^\d{1,2}-\d{1,2}-\d{4}$") {
        Write-Host "  [Date] Using specified date: $Date" -ForegroundColor Cyan
    } else {
        Write-Host "  [Date] ERROR: Invalid date format '$Date'. Use: 2026-03-12" -ForegroundColor Red
        exit 1
    }
}

$StartTime = Get-Date

# -- Paths --------------------------------------------------------------------
$Root      = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$NBADir    = Join-Path $Root "NBA"
$CBBDir    = Join-Path $Root "CBB"
$NHLDir    = Join-Path $Root "NHL"
$MLBDir    = Join-Path $Root "MLB"
$SoccerDir = Join-Path $Root "Soccer"
$TennisDir = Join-Path $Root "Tennis"
$WNBADir   = Join-Path $Root "WNBA"
$NFLDir    = Join-Path $Root "NFL"
$OutDir    = Join-Path $Root "outputs\$Date"
$WebOutDir = Join-Path $Root "ui_runner\templates"

# CBB season off  -  must match scripts/combined_slate_tickets.py (DISABLED_SPORTS / skipped CBB load).
$CBBPipelineDeactivated = $true

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

# -- Encoding -----------------------------------------------------------------
$env:PYTHONUTF8       = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 | Out-Null } catch { }

# -- Activate venv ------------------------------------------------------------
if (Test-Path (Join-Path $Root ".venv\Scripts\Activate.ps1")) {
    & (Join-Path $Root ".venv\Scripts\Activate.ps1")
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  PROP PIPELINE  -- $Date -- $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# -- Helper: auto-wipe ESPN cache if stale ------------------------------------
function Check-AutoRefreshCache {
    $cacheFile = Join-Path $NBADir "nba_espn_boxscore_cache.csv"
    if (Test-Path $cacheFile) {
        $age = (Get-Date) - (Get-Item $cacheFile).LastWriteTime
        if ($age.TotalDays -gt $CacheAgeDays) {
            Write-Host "  [Cache] ESPN cache is $([math]::Round($age.TotalDays,1)) days old (threshold: $CacheAgeDays). Auto-wiping..." -ForegroundColor Yellow
            Remove-Item (Join-Path $NBADir "nba_espn_boxscore_cache.csv") -Force -ErrorAction SilentlyContinue
            Remove-Item (Join-Path $NBADir "nba_to_espn_id_map.csv")      -Force -ErrorAction SilentlyContinue
            Write-Host "  [Cache] Wiped. Will rebuild fresh." -ForegroundColor Green
        } else {
            Write-Host "  [Cache] ESPN cache is $([math]::Round($age.TotalDays,1)) days old -- keeping." -ForegroundColor DarkGray
        }
    }
}

# -- Helper: run one step synchronously ---------------------------------------
function Run-Step {
    param(
        [string]$Label,
        [string]$Dir,
        [string]$Script,
        [string]$Arguments = ""
    )
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $Dir
    try {
        $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
        Write-Host "        CMD: $cmd" -ForegroundColor DarkGray
        $output = Invoke-Expression $cmd 2>&1
        $exit   = $LASTEXITCODE
        foreach ($line in $output) { Write-Host "        $line" -ForegroundColor DarkGray }
        if ($exit -ne 0) { Write-Host "      FAILED (exit $exit)" -ForegroundColor Red; return $false }
        Write-Host "      OK" -ForegroundColor Green; return $true
    } catch {
        Write-Host "      EXCEPTION: $_" -ForegroundColor Red; return $false
    } finally {
        Pop-Location
    }
}

# -- Helper: git push templates -----------------------------------------------
function Run-GitPush {
    Write-Host ""
    Write-Host "[ GIT ] Pushing updated templates to GitHub..." -ForegroundColor Cyan
    Push-Location $Root
    try {
        git add "ui_runner/templates/tickets_latest.html" `
                "ui_runner/templates/tickets_latest.json" `
                "ui_runner/templates/slate_latest.json" 2>&1 | Out-Null
        $msg       = "chore: pipeline update $Date $(Get-Date -Format 'HH:mm')"
        $commitOut = git commit -m $msg 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pushOut = git push origin main 2>&1
            foreach ($line in $pushOut) { Write-Host "    $line" -ForegroundColor DarkGray }
            Write-Host "  OK - Pushed to GitHub" -ForegroundColor Green
            "$Date $(Get-Date -Format 'HH:mm:ss') - PUSHED: $msg" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        } else {
            Write-Host "  (no changes to push)" -ForegroundColor DarkGray
            "$Date $(Get-Date -Format 'HH:mm:ss') - NO CHANGES" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        }
    } catch {
        Write-Host "  Git push failed: $_" -ForegroundColor Yellow
        "$Date $(Get-Date -Format 'HH:mm:ss') - PUSH FAILED: $_" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
    } finally {
        Pop-Location
    }
}

function Run-GitPushGradeArtifacts {
    param([string]$GradeDate)

    Write-Host ""
    Write-Host "[ GIT ] Pushing grade HTML for slate date $GradeDate ..." -ForegroundColor Cyan
    $candidates = @(
        "ui_runner/templates/slate_eval_$GradeDate.html",
        "ui_runner/templates/ticket_eval_$GradeDate.html",
        "ui_runner/templates/graded_props_$GradeDate.json"
    )
    $toStage = @()
    foreach ($rel in $candidates) {
        $full = Join-Path $Root ($rel -replace "/", "\")
        if (Test-Path $full) { $toStage += $rel }
    }
    if (-not $toStage.Count) {
        Write-Host "  No slate_eval / ticket_eval / graded_props found for $GradeDate  -  nothing to push" -ForegroundColor DarkGray
        "$Date $(Get-Date -Format 'HH:mm:ss') - GRADE PUSH SKIP (no grade artifacts for $GradeDate)" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        return
    }

    Push-Location $Root
    try {
        foreach ($f in $toStage) {
            git add $f 2>&1 | Out-Null
            Write-Host "    staged: $f" -ForegroundColor DarkGray
        }
        $msg       = "chore: grades $GradeDate $(Get-Date -Format 'HH:mm')"
        git commit -m $msg 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $pushOut = git push origin main 2>&1
            foreach ($line in $pushOut) { Write-Host "    $line" -ForegroundColor DarkGray }
            Write-Host "  OK - Grade HTML pushed" -ForegroundColor Green
            "$Date $(Get-Date -Format 'HH:mm:ss') - GRADE PUSHED: $msg" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
        } else {
            Write-Host "  No git changes for grade HTML (already committed?)" -ForegroundColor DarkGray
            $unpushed = git log origin/main..HEAD --oneline 2>&1
            if ($unpushed) {
                git push origin main 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Host "  OK - Flushed pending commits" -ForegroundColor Green }
            }
        }
    } catch {
        Write-Host "  Grade push exception: $_" -ForegroundColor Red
    } finally {
        Pop-Location
    }
}

function Run-PostPipelineGrader {
    if ($SkipDailyGrader) {
        Write-Host "`n[ GRADES ] SkipDailyGrader  -  not running post-pipeline grader" -ForegroundColor DarkGray
        return
    }
    try {
        $dt = [datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {
        Write-Host "`n[ GRADES ] Could not parse pipeline date '$Date'  -  skip grader" -ForegroundColor Yellow
        return
    }
    $gradeDate = $dt.AddDays(-1).ToString("yyyy-MM-dd")
    Write-Host ""
    Write-Host "[ GRADES ] Post-pipeline grader (slate date $gradeDate)" -ForegroundColor Magenta

    $runner = Join-Path $Root "scripts\run_grader.ps1"
    if (-not (Test-Path $runner)) {
        Write-Host "  scripts\run_grader.ps1 not found  -  skip" -ForegroundColor Yellow
        return
    }
    & $runner -Date $gradeDate
    Run-GitPushGradeArtifacts -GradeDate $gradeDate
}

# -- Helper: run combined, auto-detect all sports on disk ---------------------
function Run-Combined {
    param([string]$Reason = "")
    Write-Host ""
    $label = if ($Reason) { "[ COMBINED SLATE -- $Reason ]" } else { "[ COMBINED SLATE ]" }
    Write-Host $label -ForegroundColor Magenta
    Write-Host ""

    # Clean up any stale root-level combined_slate_tickets files from previous runs
    Get-ChildItem -Path $Root -Filter "combined_slate_tickets_*.xlsx" | Remove-Item -Force -ErrorAction SilentlyContinue

    $nbaFile    = "$NBADir\data\outputs\step8_all_direction_clean.xlsx"
    if (-not (Test-Path $nbaFile)) {
        $nbaAlt = Join-Path $NBADir "step8_all_direction_clean.xlsx"
        if (Test-Path $nbaAlt) {
            $nbaFile = $nbaAlt
            Write-Host "  [NBA] Using fallback step8: $nbaFile" -ForegroundColor DarkGray
        }
    }
    $cbbFile    = "$CBBDir\step6_ranked_cbb.xlsx"
    $nhlFile    = "$NHLDir\outputs\step8_nhl_direction_clean.xlsx"
    $soccerFile = "$SoccerDir\outputs\step8_soccer_direction_clean.xlsx"
    $mlbFile    = "$MLBDir\step8_mlb_direction_clean.xlsx"
    $tennisFile = "$TennisDir\outputs\step8_tennis_direction_clean.xlsx"

    if (-not (Test-Path $nbaFile)) { Write-Host "  WARNING: NBA step8 not found -- skipping combined" -ForegroundColor Yellow; return $false }
    if (-not $CBBPipelineDeactivated) {
        if (-not (Test-Path $cbbFile)) { Write-Host "  WARNING: CBB step6 not found -- skipping combined" -ForegroundColor Yellow; return $false }
    }

    $CombinedOut  = Join-Path $Root "combined_slate_tickets_$Date.xlsx"
    $CombinedArgs  = "--nba `"$nbaFile`""
    if ($CBBPipelineDeactivated) {
        Write-Host "  [ ] CBB (season deactivated  -  combined skips CBB slate)" -ForegroundColor DarkGray
    } else {
        $CombinedArgs += " --cbb `"$cbbFile`""
        Write-Host "  [+] CBB" -ForegroundColor DarkGray
    }

    if (Test-Path $nhlFile)    { $CombinedArgs += " --nhl `"$nhlFile`"";       Write-Host "  [+] NHL"    -ForegroundColor DarkGray }
    if (Test-Path $soccerFile) { $CombinedArgs += " --soccer `"$soccerFile`""; Write-Host "  [+] Soccer" -ForegroundColor DarkGray }
    if (Test-Path $mlbFile)    { $CombinedArgs += " --mlb `"$mlbFile`"";       Write-Host "  [+] MLB"    -ForegroundColor DarkGray }
    if (Test-Path $tennisFile) { $CombinedArgs += " --tennis `"$tennisFile`""; Write-Host "  [+] Tennis" -ForegroundColor DarkGray }

    $CombinedArgs += " --date $Date --output `"$CombinedOut`" --tiers A,B,C,D --max-tickets 3 --write-web --web-outdir `"$WebOutDir`""

    $okC = Run-Step "Combined Slate + Tickets" $Root ".\scripts\combined_slate_tickets.py" $CombinedArgs

    if ($okC) {
        Copy-Item $CombinedOut (Join-Path $OutDir "combined_slate_tickets_$Date.xlsx") -Force -ErrorAction SilentlyContinue
        Remove-Item $CombinedOut -Force -ErrorAction SilentlyContinue
        Write-Host "  Saved -> $(Join-Path $OutDir "combined_slate_tickets_$Date.xlsx")" -ForegroundColor Green
        Run-GitPush
        Run-PostPipelineGrader
    } else {
        Write-Host "  Combined FAILED" -ForegroundColor Red
    }
    Write-Host ""
    return $okC
}

# -- Helper: print elapsed + done banner --------------------------------------
function Print-Done {
    $Elapsed = (Get-Date) - $StartTime
    Write-Host "======================================================" -ForegroundColor Cyan
    Write-Host ("  DONE  -- Elapsed: {0}" -f $Elapsed.ToString("mm\:ss")) -ForegroundColor Cyan
    Write-Host "======================================================" -ForegroundColor Cyan
    Write-Host ""
}

# =============================================================================
#  COMBINED ONLY  -- picks up every sport already on disk
# =============================================================================
if ($CombinedOnly) {
    Run-Combined "from existing outputs"
    Print-Done
    exit
}

# =============================================================================
#  WNBA ONLY
# =============================================================================
if ($WNBAOnly) {
    Write-Host "[ WNBA PIPELINE ]" -ForegroundColor Magenta
    Write-Host "  Delegating to run_wnba_pipeline.ps1 ..." -ForegroundColor DarkGray
    & (Join-Path $Root "run_wnba_pipeline.ps1") -Date $Date
    Print-Done
    exit
}

# =============================================================================
#  NHL ONLY
# =============================================================================
if ($NHLOnly) {
    Write-Host "[ NHL PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"         "--output outputs\step1_nhl_props.csv" } } else { Write-Host "  [NHL] Skipping step1 fetch -- using existing outputs\step1_nhl_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input outputs\step1_nhl_props.csv --output outputs\step2_nhl_picktypes.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input outputs\step2_nhl_picktypes.csv --output outputs\step3_nhl_with_defense.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input outputs\step3_nhl_with_defense.csv --output outputs\step4_nhl_with_stats.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input outputs\step4_nhl_with_stats.csv --output outputs\step5_nhl_hit_rates.csv --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input outputs\step5_nhl_hit_rates.csv --output outputs\step6_nhl_role_context.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input outputs\step6_nhl_role_context.csv --output outputs\step7_nhl_ranked.xlsx" }
    if ($ok) { $ok = Run-Step "NHL Step 8 - Direction Context"  $NHLDir ".\scripts\step8_add_direction_context_nhl.py"  "--input outputs\step7_nhl_ranked.xlsx --output outputs\step8_nhl_direction_clean.xlsx" }
    Write-Host ""
    if ($ok) { Write-Host "  NHL complete." -ForegroundColor Green } else { Write-Host "  NHL FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after NHL" }
    Print-Done
    exit
}

# =============================================================================
#  MLB ONLY
# =============================================================================
if ($MLBOnly) {
    Write-Host "[ MLB PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "MLB Step 1 - Fetch PrizePicks" $MLBDir ".\scripts\step1_fetch_prizepicks_mlb.py" "--output step1_mlb_props.csv --date $Date" } } else { Write-Host "  [MLB] Skipping step1 fetch -- using existing step1_mlb_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "MLB Step 2 - Attach Pick Types"  $MLBDir ".\step2_attach_picktypes_mlb.py"       "--input step1_mlb_props.csv --output step2_mlb_picktypes.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 3 - Attach Defense"     $MLBDir ".\step3_attach_defense_mlb.py"         "--input step2_mlb_picktypes.csv --defense mlb_defense_summary.csv --output step3_mlb_with_defense.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 4 - Player Stats"       $MLBDir ".\step4_attach_player_stats_mlb.py"    "--input step3_mlb_with_defense.csv --cache mlb_stats_cache.csv --output step4_mlb_with_stats.csv --season 2025" }
    if ($ok) { $ok = Run-Step "MLB Step 5 - Line Hit Rates"     $MLBDir ".\step5_add_line_hit_rates_mlb.py"     "--input step4_mlb_with_stats.csv --output step5_mlb_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 6 - Team Role Context"  $MLBDir ".\step6_team_role_context_mlb.py"      "--input step5_mlb_hit_rates.csv --output step6_mlb_role_context.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 7 - Rank Props"         $MLBDir ".\step7_rank_props_mlb.py"             "--input step6_mlb_role_context.csv --output step7_mlb_ranked.xlsx" }
    if ($ok) { $ok = Run-Step "MLB Step 8 - Direction Context"  $MLBDir ".\step8_add_direction_context_mlb.py"  "--input step7_mlb_ranked.xlsx --output step8_mlb_direction_clean.xlsx" }
    Write-Host ""
    if ($ok) { Write-Host "  MLB complete." -ForegroundColor Green } else { Write-Host "  MLB FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after MLB" }
    Print-Done
    exit
}

# =============================================================================
#  SOCCER ONLY
# =============================================================================
if ($SoccerOnly) {
    Write-Host "[ SOCCER PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output outputs\step1_soccer_props.csv --date $Date" } } else { Write-Host "  [Soccer] Skipping step1 fetch -- using existing outputs\step1_soccer_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input outputs\step1_soccer_props.csv --output outputs\step2_soccer_picktypes.csv" }
    if ($ok) { $ok = Run-Step "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input outputs\step2_soccer_picktypes.csv --defense cache\soccer_defense_summary.csv --output outputs\step3_soccer_with_defense.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input outputs\step3_soccer_with_defense.csv --output outputs\step4_soccer_with_stats.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input outputs\step4_soccer_with_stats.csv --output outputs\step5_soccer_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input outputs\step5_soccer_hit_rates.csv --output outputs\step6_soccer_role_context.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input outputs\step6_soccer_role_context.csv --output outputs\step7_soccer_ranked.xlsx" }
    if ($ok) { $ok = Run-Step "Soccer Step 7b - Edge Score"        $Root      ".\scripts\step7b_edge_score.py"                  "--sport Soccer --step7-xlsx `"$SoccerDir\outputs\step7_soccer_ranked.xlsx`" --repo-root `"$Root`"" }
    if ($ok) { $ok = Run-Step "Soccer Step 8 - Direction Context"  $SoccerDir ".\scripts\step8_add_direction_context_soccer.py"  "--input outputs\step7_soccer_ranked.xlsx --sheet ALL --output outputs\step8_soccer_direction.csv --xlsx outputs\step8_soccer_direction_clean.xlsx --date $Date" }
    if ($ok) { $ok = Run-Step "Soccer Step 8b - Health Check"      $SoccerDir ".\scripts\healthcheck_soccer_directions.py"      "--step7 outputs\step7_soccer_ranked.xlsx --step8 outputs\step8_soccer_direction_clean.xlsx" }
    Write-Host ""
    if ($ok) { Write-Host "  Soccer complete." -ForegroundColor Green } else { Write-Host "  Soccer FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after Soccer" }
    Print-Done
    exit
}

# =============================================================================
#  TENNIS ONLY  (PrizePicks league_id 5 = TENNIS)
# =============================================================================
if ($TennisOnly) {
    Write-Host "[ TENNIS PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    $ok = $true
    if (-not $SkipFetch) {
        if ($ok) {
            $ok = Run-Step "Tennis Step 1 - Fetch PrizePicks" $TennisDir ".\scripts\step1_fetch_prizepicks_tennis.py" "--league_id 5 --output outputs\step1_tennis_props.csv"
        }
    } else {
        Write-Host "  [Tennis] Skipping step1 fetch -- using existing outputs\step1_tennis_props.csv" -ForegroundColor DarkGray
    }
    if ($ok) { $ok = Run-Step "Tennis Step 2 - Attach Pick Types"   $TennisDir ".\scripts\step2_attach_picktypes_tennis.py"   "--input outputs\step1_tennis_props.csv --output outputs\step2_tennis_picktypes.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 3 - Defense Rankings"   $TennisDir ".\scripts\step3_defense_rankings_tennis.py"   "--input outputs\step2_tennis_picktypes.csv --output outputs\step3_tennis_with_defense.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 4 - Player Stats"        $TennisDir ".\scripts\step4_attach_player_stats_tennis.py" "--input outputs\step3_tennis_with_defense.csv --output outputs\step4_tennis_with_stats.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 5 - Line Hit Rates"      $TennisDir ".\scripts\step5_compute_hitrates_tennis.py"    "--input outputs\step4_tennis_with_stats.csv --output outputs\step5_tennis_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 6 - Role Context"       $TennisDir ".\scripts\step6_add_context_tennis.py"         "--input outputs\step5_tennis_hit_rates.csv --output outputs\step6_tennis_role_context.csv" }
    if ($ok) { $ok = Run-Step "Tennis Step 7 - Rank Props"         $TennisDir ".\scripts\step7_rank_props_tennis.py"          "--input outputs\step6_tennis_role_context.csv --output outputs\step7_tennis_ranked.xlsx" }
    if ($ok) { $ok = Run-Step "Tennis Step 8 - Direction Context"  $TennisDir ".\scripts\step8_add_direction_context_tennis.py" "--input outputs\step7_tennis_ranked.xlsx --sheet ALL --output outputs\step8_tennis_direction.csv --xlsx outputs\step8_tennis_direction_clean.xlsx --date $Date" }
    Write-Host ""
    if ($ok) { Write-Host "  Tennis complete." -ForegroundColor Green } else { Write-Host "  Tennis FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after Tennis" }
    Print-Done
    exit
}

# =============================================================================
#  CBB ONLY
# =============================================================================
if ($CBBOnly) {
    Write-Host "[ CBB PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    if ($CBBPipelineDeactivated) {
        Write-Host "  CBB is deactivated for the season (no steps 1-6). Running combined only." -ForegroundColor Yellow
        Write-Host ""
        Run-Combined "CBB deactivated (CBBOnly)"
        Print-Done
        exit
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "CBB Step 1 - Fetch PrizePicks"      $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py"      "--out step1_cbb.csv" } } else { Write-Host "  [CBB] Skipping step1 fetch -- using existing step1_cbb.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input step1_cbb.csv --output step2_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input step2_cbb.csv --defense data\reference\cbb_def_rankings.csv --output step3b_with_def_rankings_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input step3b_with_def_rankings_cbb.csv --output step3_cbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input step3_cbb.csv --output step5b_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input step5b_cbb.csv --output step6_ranked_cbb.xlsx" }
    Write-Host ""
    if ($ok) { Write-Host "  CBB complete." -ForegroundColor Green } else { Write-Host "  CBB FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after CBB" }
    Print-Done
    exit
}

# =============================================================================
#  NBA ONLY
# =============================================================================
if ($NBAOnly) {
    Write-Host "[ NBA PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""

    if (Test-Path (Join-Path $NBADir "RUN_COMPLETE.flag")) { Remove-Item (Join-Path $NBADir "RUN_COMPLETE.flag") -Force }

    if ($RefreshCache) {
        Write-Host "  [Cache] Wiping ESPN cache files..." -ForegroundColor Yellow
        Remove-Item (Join-Path $NBADir "nba_espn_boxscore_cache.csv") -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $NBADir "nba_to_espn_id_map.csv")      -Force -ErrorAction SilentlyContinue
        Write-Host "  [Cache] Done." -ForegroundColor Green
        Write-Host ""
    } else {
        Check-AutoRefreshCache
    }

    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NBA Step 1 - Fetch PrizePicks"    $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output data\outputs\step1_pp_props_today.csv --date $Date" } } else { Write-Host "  [NBA] Skipping step1 fetch -- using existing data\outputs\step1_pp_props_today.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input data\outputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input data\outputs\step2_with_picktypes.csv --defense data\cache\defense_team_summary.csv --output data\outputs\step3_with_defense.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate data\outputs\step3_with_defense.csv --out data\outputs\step4_with_stats.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input data\outputs\step4_with_stats.csv --output data\outputs\step5_with_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input data\outputs\step5_with_hit_rates.csv --output data\outputs\step6_with_team_role_context.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input data\outputs\step6_with_team_role_context.csv --output data\outputs\step6a_with_opp_stats.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input data\outputs\step6a_with_opp_stats.csv --output data\outputs\step6b_with_game_context.csv --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input data\outputs\step6b_with_game_context.csv --output data\outputs\step6c_with_schedule_flags.csv --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input data\outputs\step6c_with_schedule_flags.csv --output data\outputs\step6d_with_h2h.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input data\outputs\step6d_with_h2h.csv --output data\outputs\step7_ranked_props.xlsx" }
    if ($ok) { $ok = Run-Step "NBA Step 8 - Direction Context"       $NBADir ".\scripts\step8_add_direction_context.py"         "--input data\outputs\step7_ranked_props.xlsx --sheet ALL --output data\outputs\step8_all_direction.csv" }

    if ($ok) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }
    Write-Host ""
    if ($ok) { Write-Host "  NBA complete." -ForegroundColor Green } else { Write-Host "  NBA FAILED." -ForegroundColor Red }
    if ($ok) { Run-Combined "after NBA" }
    Print-Done
    exit
}

# =============================================================================
#  FULL PARALLEL RUN  (NBA + CBB + NHL + Soccer always)
# =============================================================================
if ($RefreshCache) {
    Write-Host "  [Cache] Wiping ESPN cache files..." -ForegroundColor Yellow
    Remove-Item (Join-Path $NBADir "nba_espn_boxscore_cache.csv") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $NBADir "nba_to_espn_id_map.csv")      -Force -ErrorAction SilentlyContinue
    Write-Host "  [Cache] Done." -ForegroundColor Green
    Write-Host ""
} else {
    Check-AutoRefreshCache
}

if (Test-Path (Join-Path $NBADir "RUN_COMPLETE.flag")) { Remove-Item (Join-Path $NBADir "RUN_COMPLETE.flag") -Force }

# -- Backfill boxscore DB for last 3 days (all sports) ------------------------
Write-Host "[ DB BACKFILL ]" -ForegroundColor Cyan
Write-Host "  Syncing proporacle_ref.db for last 3 days..." -ForegroundColor DarkGray
$backfillScript = Join-Path $NBADir "scripts\build_boxscore_ref.py"
if (Test-Path $backfillScript) {
    $backfillOut = Invoke-Expression "py -3.14 `"$backfillScript`" --backfill --days 3 --sports nba cbb nhl soccer" 2>&1
    foreach ($line in $backfillOut) { Write-Host "  $line" -ForegroundColor DarkGray }
    Write-Host "  DB backfill complete." -ForegroundColor Green
} else {
    Write-Host "  WARNING: build_boxscore_ref.py not found -- skipping backfill" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "[ PARALLEL PIPELINE: NBA + CBB + NHL + Soccer + Tennis ]" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Starting all pipelines simultaneously..." -ForegroundColor Cyan
Write-Host ""

# -- NBA Job ------------------------------------------------------------------
$NBAJob = Start-Job -ScriptBlock {
    param($NBADir, $Date, $OddsApiKey, $SkipFetch)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[NBA] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[NBA] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[NBA] OK: $Label"; return $true
        } catch { Write-Output "[NBA] EXCEPTION in $Label`: $_"; return $false
        } finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NBA Step 1 - Fetch PrizePicks"    $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output data\outputs\step1_pp_props_today.csv --date $Date" } } else { Write-Output "[NBA] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input data\outputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input data\outputs\step2_with_picktypes.csv --defense data\cache\defense_team_summary.csv --output data\outputs\step3_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate data\outputs\step3_with_defense.csv --out data\outputs\step4_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input data\outputs\step4_with_stats.csv --output data\outputs\step5_with_hit_rates.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input data\outputs\step5_with_hit_rates.csv --output data\outputs\step6_with_team_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input data\outputs\step6_with_team_role_context.csv --output data\outputs\step6a_with_opp_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input data\outputs\step6a_with_opp_stats.csv --output data\outputs\step6b_with_game_context.csv --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input data\outputs\step6b_with_game_context.csv --output data\outputs\step6c_with_schedule_flags.csv --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input data\outputs\step6c_with_schedule_flags.csv --output data\outputs\step6d_with_h2h.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input data\outputs\step6d_with_h2h.csv --output data\outputs\step7_ranked_props.xlsx" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 8 - Direction Context"       $NBADir ".\scripts\step8_add_direction_context.py"         "--input data\outputs\step7_ranked_props.xlsx --sheet ALL --output data\outputs\step8_all_direction.csv" }
    return $ok
} -ArgumentList $NBADir, $Date, $OddsApiKey, $SkipFetch

# -- CBB Job ------------------------------------------------------------------
$CBBJob = Start-Job -ScriptBlock {
    param($CBBDir, $SkipFetch, $CBBDeactivated)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    if ($CBBDeactivated) {
        Write-Output "[CBB] Skipped (season deactivated  -  steps 1-6 not run; combined omits CBB)."
        return $true
    }
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[CBB] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[CBB] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[CBB] OK: $Label"; return $true
        } catch { Write-Output "[CBB] EXCEPTION in $Label`: $_"; return $false
        } finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "CBB Step 1 - Fetch PrizePicks" $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py" "--out step1_cbb.csv" } } else { Write-Output "[CBB] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input step1_cbb.csv --output step2_cbb.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input step2_cbb.csv --defense data\reference\cbb_def_rankings.csv --output step3b_with_def_rankings_cbb.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input step3b_with_def_rankings_cbb.csv --output step3_cbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input step3_cbb.csv --output step5b_cbb.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input step5b_cbb.csv --output step6_ranked_cbb.xlsx" }
    return $ok
} -ArgumentList $CBBDir, $SkipFetch, $CBBPipelineDeactivated

# -- NHL Job ------------------------------------------------------------------
$NHLJob = Start-Job -ScriptBlock {
    param($NHLDir, $SkipFetch)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[NHL] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[NHL] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[NHL] OK: $Label"; return $true
        } catch { Write-Output "[NHL] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"        "--output outputs\step1_nhl_props.csv" } } else { Write-Output "[NHL] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input outputs\step1_nhl_props.csv --output outputs\step2_nhl_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input outputs\step2_nhl_picktypes.csv --output outputs\step3_nhl_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input outputs\step3_nhl_with_defense.csv --output outputs\step4_nhl_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input outputs\step4_nhl_with_stats.csv --output outputs\step5_nhl_hit_rates.csv --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input outputs\step5_nhl_hit_rates.csv --output outputs\step6_nhl_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input outputs\step6_nhl_role_context.csv --output outputs\step7_nhl_ranked.xlsx" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 8 - Direction Context"  $NHLDir ".\scripts\step8_add_direction_context_nhl.py"  "--input outputs\step7_nhl_ranked.xlsx --output outputs\step8_nhl_direction_clean.xlsx" }
    return $ok
} -ArgumentList $NHLDir, $SkipFetch

# -- Soccer Job ---------------------------------------------------------------
$SoccerJob = Start-Job -ScriptBlock {
    param($SoccerDir, $Date, $SkipFetch)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[SOCCER] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[SOCCER] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[SOCCER] OK: $Label"; return $true
        } catch { Write-Output "[SOCCER] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output outputs\step1_soccer_props.csv --date $Date" } } else { Write-Output "[Soccer] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input outputs\step1_soccer_props.csv --output outputs\step2_soccer_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input outputs\step2_soccer_picktypes.csv --defense cache\soccer_defense_summary.csv --output outputs\step3_soccer_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input outputs\step3_soccer_with_defense.csv --output outputs\step4_soccer_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input outputs\step4_soccer_with_stats.csv --output outputs\step5_soccer_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input outputs\step5_soccer_hit_rates.csv --output outputs\step6_soccer_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input outputs\step6_soccer_role_context.csv --output outputs\step7_soccer_ranked.xlsx" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 7b - Edge Score"        $Root      ".\scripts\step7b_edge_score.py"                  "--sport Soccer --step7-xlsx `"$SoccerDir\outputs\step7_soccer_ranked.xlsx`" --repo-root `"$Root`"" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 8 - Direction Context"  $SoccerDir ".\scripts\step8_add_direction_context_soccer.py"  "--input outputs\step7_soccer_ranked.xlsx --sheet ALL --output outputs\step8_soccer_direction.csv --xlsx outputs\step8_soccer_direction_clean.xlsx --date $Date" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 8b - Health Check"      $SoccerDir ".\scripts\healthcheck_soccer_directions.py"      "--step7 outputs\step7_soccer_ranked.xlsx --step8 outputs\step8_soccer_direction_clean.xlsx" }
    return $ok
} -ArgumentList $SoccerDir, $Date, $SkipFetch

# -- Tennis Job ---------------------------------------------------------------
$TennisJob = Start-Job -ScriptBlock {
    param($TennisDir, $Date, $SkipFetch)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[TENNIS] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[TENNIS] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[TENNIS] OK: $Label"; return $true
        } catch { Write-Output "[TENNIS] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) {
        if ($ok) { $ok = Run-Step-Job "Tennis Step 1 - Fetch PrizePicks" $TennisDir ".\scripts\step1_fetch_prizepicks_tennis.py" "--league_id 5 --output outputs\step1_tennis_props.csv" }
    } else {
        Write-Output "[Tennis] Skipping step1 fetch"
    }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 2 - Attach Pick Types"   $TennisDir ".\scripts\step2_attach_picktypes_tennis.py"   "--input outputs\step1_tennis_props.csv --output outputs\step2_tennis_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 3 - Defense Rankings"   $TennisDir ".\scripts\step3_defense_rankings_tennis.py"   "--input outputs\step2_tennis_picktypes.csv --output outputs\step3_tennis_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 4 - Player Stats"        $TennisDir ".\scripts\step4_attach_player_stats_tennis.py" "--input outputs\step3_tennis_with_defense.csv --output outputs\step4_tennis_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 5 - Line Hit Rates"      $TennisDir ".\scripts\step5_compute_hitrates_tennis.py"    "--input outputs\step4_tennis_with_stats.csv --output outputs\step5_tennis_hit_rates.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 6 - Role Context"       $TennisDir ".\scripts\step6_add_context_tennis.py"         "--input outputs\step5_tennis_hit_rates.csv --output outputs\step6_tennis_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 7 - Rank Props"         $TennisDir ".\scripts\step7_rank_props_tennis.py"          "--input outputs\step6_tennis_role_context.csv --output outputs\step7_tennis_ranked.xlsx" }
    if ($ok) { $ok = Run-Step-Job "Tennis Step 8 - Direction Context"  $TennisDir ".\scripts\step8_add_direction_context_tennis.py" "--input outputs\step7_tennis_ranked.xlsx --sheet ALL --output outputs\step8_tennis_direction.csv --xlsx outputs\step8_tennis_direction_clean.xlsx --date $Date" }
    return $ok
} -ArgumentList $TennisDir, $Date, $SkipFetch

# -- NFL Job (INACTIVE until Sept 2026 regular season) -------------------------
# NFL — activate September 2026 for regular season
# Uncomment when step6 historical data is populated and step8 exists.
# $NFLJob = Start-Job -ScriptBlock {
#     param($NFLDir, $Date, $SkipFetch)
#     $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
#     $env:NFL_PIPELINE_ACTIVE = "1"
#     function Run-Step-Job {
#         param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
#         Write-Output "[NFL] --> $Label"
#         Push-Location $Dir
#         try {
#             $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
#             Write-Output "        CMD: $cmd"
#             $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
#             foreach ($line in $output) { Write-Output "        $line" }
#             if ($exit -ne 0) { Write-Output "[NFL] FAILED: $Label (exit $exit)"; return $false }
#             Write-Output "[NFL] OK: $Label"; return $true
#         } catch { Write-Output "[NFL] EXCEPTION: $_"; return $false
#         } finally { Pop-Location }
#     }
#     $ok = $true
#     if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NFL Step 1 - Fetch PrizePicks" $NFLDir ".\scripts\step1_fetch_prizepicks_nfl.py" "--output data\outputs\step1_pp_props_today.csv --date $Date" } } else { Write-Output "[NFL] Skipping step1 fetch" }
#     if ($ok) { $ok = Run-Step-Job "NFL Step 2 - Clean Props" $NFLDir ".\scripts\step2_clean_props.py" "" }
#     if ($ok) { $ok = Run-Step-Job "NFL Step 4 - Defense Rankings" $NFLDir ".\scripts\step4_defense_rankings.py" "" }
#     if ($ok) { $ok = Run-Step-Job "NFL Step 6 - Hit Rates (skeleton)" $NFLDir ".\scripts\step6_historical_hit_rates.py" "" }
#     return $ok
# } -ArgumentList $NFLDir, $Date, $SkipFetch

# -- Wait + stream output -----------------------------------------------------
$allJobs = @($NBAJob, $CBBJob, $NHLJob, $SoccerJob, $TennisJob)

Write-Host "  [Waiting for all pipelines to finish...]" -ForegroundColor DarkGray
Write-Host ""

while (($allJobs | Where-Object { $_.State -eq 'Running' }).Count -gt 0) {
    foreach ($job in $allJobs) { $out = Receive-Job $job -ErrorAction SilentlyContinue; foreach ($line in $out) { Write-Host "    $line" -ForegroundColor DarkGray } }
    Start-Sleep -Milliseconds 500
}
foreach ($job in $allJobs) { $out = Receive-Job $job -ErrorAction SilentlyContinue; foreach ($line in $out) { Write-Host "    $line" -ForegroundColor DarkGray } }

# -- Results ------------------------------------------------------------------
$NBASuccess    = Test-Path (Join-Path $NBADir    "data\outputs\step8_all_direction_clean.xlsx")
$CBBSuccess    = if ($CBBPipelineDeactivated) { $true } else { Test-Path (Join-Path $CBBDir "step6_ranked_cbb.xlsx") }
$NHLSuccess    = Test-Path (Join-Path $NHLDir    "outputs\step8_nhl_direction_clean.xlsx")
$SoccerSuccess = Test-Path (Join-Path $SoccerDir "outputs\step8_soccer_direction_clean.xlsx")
$TennisSuccess = Test-Path (Join-Path $TennisDir "outputs\step8_tennis_direction_clean.xlsx")

Remove-Job $allJobs -Force -ErrorAction SilentlyContinue
if ($NBASuccess) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }

Write-Host ""
@(
    @{ Name="NBA";    Ok=$NBASuccess },
    @{ Name="CBB";    Ok=$CBBSuccess },
    @{ Name="NHL";    Ok=$NHLSuccess },
    @{ Name="Soccer"; Ok=$SoccerSuccess },
    @{ Name="Tennis"; Ok=$TennisSuccess }
) | ForEach-Object {
    if ($_.Name -eq "CBB" -and $CBBPipelineDeactivated) {
        Write-Host "  CBB skipped (season deactivated)." -ForegroundColor DarkGray
    } elseif ($_.Ok) {
        Write-Host "  $($_.Name) complete." -ForegroundColor Green
    } else {
        Write-Host "  $($_.Name) FAILED."  -ForegroundColor Red
    }
}

Run-Combined "full parallel run"
Print-Done
