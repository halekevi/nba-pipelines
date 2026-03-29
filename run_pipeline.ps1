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
#    .\run_pipeline.ps1 -WNBAOnly              # WNBA only (season-gated)
#    .\run_pipeline.ps1 -CombinedOnly          # Re-run combined using all existing outputs
#    .\run_pipeline.ps1 -SkipFetch             # Skip step1 fetch for whatever sport(s) run
#    .\run_pipeline.ps1 -NBAOnly -SkipFetch    # NBA steps 2-8 + Combined
#    .\run_pipeline.ps1 -NHLOnly -SkipFetch    # NHL steps 2-8 + Combined
#    .\run_pipeline.ps1 -SoccerOnly -SkipFetch # Soccer steps 2-8 + Combined
#    .\run_pipeline.ps1 -RefreshCache          # Wipe + rebuild ESPN cache before NBA
#    .\run_pipeline.ps1 -CacheAgeDays 7        # Auto-wipe cache if older than N days
#
#  Combined always auto-includes every sport whose step8 output exists on disk.
#  No -Include flags needed -- just run any sport, combined picks it up.
# ============================================================
param(
    [string]$Date       = "",
    [string]$OddsApiKey = "10b3aa326aaec16be06e0fd074ed4ed9",
    [switch]$NBAOnly,
    [switch]$CBBOnly,
    [switch]$NHLOnly,
    [switch]$MLBOnly,
    [switch]$SoccerOnly,
    [switch]$WNBAOnly,
    [switch]$CombinedOnly,
    [switch]$SkipCombined,
    [switch]$SkipFetch,
    [switch]$RefreshCache,
    [switch]$ForceAll,
    [switch]$DQWarnOnly,
    [int]$CacheAgeDays = 7
)

$ErrorActionPreference = "Continue"

# -- ENV CHECK (helps debug scheduled task context) -----------------------------
Write-Host "=== ENV CHECK ===" -ForegroundColor DarkGray
Write-Host "LOCALAPPDATA: $env:LOCALAPPDATA" -ForegroundColor DarkGray
Write-Host "USERPROFILE:  $env:USERPROFILE" -ForegroundColor DarkGray
Write-Host "USERNAME:     $env:USERNAME" -ForegroundColor DarkGray
Write-Host "SESSION:      $env:SESSIONNAME" -ForegroundColor DarkGray
try {
    $resolvedCache = py -3.14 -c "from scripts.ensure_local_cache import ensure_local_cache; print(ensure_local_cache())"
    Write-Host "cache_dir:    $resolvedCache" -ForegroundColor DarkGray
} catch {
    Write-Host "cache_dir:    (error resolving via ensure_local_cache)" -ForegroundColor DarkGray
}
Write-Host "=================" -ForegroundColor DarkGray

# -- Date ---------------------------------------------------------------------
if (-not $Date) {
    $Date = Get-Date -Format "yyyy-MM-dd"
    Write-Host "  [Date] No date specified, using today: $Date" -ForegroundColor DarkGray
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
$Root      = Split-Path -Parent $MyInvocation.MyCommand.Definition
$NBADir    = Join-Path $Root "NBA"
$CBBDir    = Join-Path $Root "CBB"
$NHLDir    = Join-Path $Root "NHL"
$MLBDir    = Join-Path $Root "MLB"
$SoccerDir = Join-Path $Root "Soccer"
$WNBADir   = Join-Path $Root "WNBA"
$OutDir    = Join-Path $Root "outputs\$Date"
$WebOutDir = Join-Path $Root "ui_runner\templates"

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
        # Always add the 3 core "latest" files (always rewritten by pipeline)
        $filesToAdd = @(
            "ui_runner/templates/tickets_latest.html",
            "ui_runner/templates/tickets_latest.json",
            "ui_runner/templates/slate_latest.json"
        )

        # Add dated eval files only if they actually exist (may not exist on every run mode)
        $slateEval  = "ui_runner/templates/slate_eval_$Date.html"
        $ticketEval = "ui_runner/templates/ticket_eval_$Date.html"
        if (Test-Path (Join-Path $Root $slateEval.Replace("/","\")))  { $filesToAdd += $slateEval  }
        if (Test-Path (Join-Path $Root $ticketEval.Replace("/","\"))) { $filesToAdd += $ticketEval }

        # Verify the core files actually exist before trying to push
        $missing = $filesToAdd | Where-Object {
            -not (Test-Path (Join-Path $Root $_.Replace("/","\")))
        }
        if ($missing) {
            Write-Host "  WARNING: Missing template files, skipping push:" -ForegroundColor Yellow
            $missing | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
            "$Date $(Get-Date -Format 'HH:mm:ss') - SKIPPED (missing files: $($missing -join ', '))" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
            return
        }

        # Stage all files
        foreach ($f in $filesToAdd) {
            git add $f 2>&1 | Out-Null
            Write-Host "    staged: $f" -ForegroundColor DarkGray
        }

        $msg       = "chore: pipeline update $Date $(Get-Date -Format 'HH:mm')"
        $commitOut = git commit -m $msg 2>&1
        $commitExit = $LASTEXITCODE

        if ($commitExit -eq 0) {
            Write-Host "  Committed. Pushing to origin/main..." -ForegroundColor DarkGray
            $pushOut = git push origin main 2>&1
            $pushExit = $LASTEXITCODE
            foreach ($line in $pushOut) { Write-Host "    $line" -ForegroundColor DarkGray }
            if ($pushExit -eq 0) {
                Write-Host "  OK - Pushed to GitHub -> Railway will redeploy" -ForegroundColor Green
                "$Date $(Get-Date -Format 'HH:mm:ss') - PUSHED: $msg" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
            } else {
                Write-Host "  PUSH FAILED (exit $pushExit) -- check git credentials" -ForegroundColor Red
                "$Date $(Get-Date -Format 'HH:mm:ss') - PUSH FAILED (exit $pushExit): $($pushOut -join ' | ')" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
            }
        } else {
            # Nothing new to commit -- but still push in case a prior commit wasn't pushed
            Write-Host "  No new changes to commit. Checking if unpushed commits exist..." -ForegroundColor DarkGray
            $unpushed = git log origin/main..HEAD --oneline 2>&1
            if ($unpushed) {
                Write-Host "  Found unpushed commits -- pushing now..." -ForegroundColor Yellow
                $pushOut = git push origin main 2>&1
                $pushExit = $LASTEXITCODE
                foreach ($line in $pushOut) { Write-Host "    $line" -ForegroundColor DarkGray }
                if ($pushExit -eq 0) {
                    Write-Host "  OK - Flushed pending commits to GitHub" -ForegroundColor Green
                    "$Date $(Get-Date -Format 'HH:mm:ss') - PUSHED PENDING: $($unpushed -join '; ')" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
                } else {
                    Write-Host "  PUSH FAILED (exit $pushExit)" -ForegroundColor Red
                    "$Date $(Get-Date -Format 'HH:mm:ss') - PUSH FAILED on pending (exit $pushExit)" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
                }
            } else {
                Write-Host "  Already up to date on origin/main." -ForegroundColor DarkGray
                "$Date $(Get-Date -Format 'HH:mm:ss') - NO CHANGES (already up to date)" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
            }
        }
    } catch {
        Write-Host "  Git push exception: $_" -ForegroundColor Red
        "$Date $(Get-Date -Format 'HH:mm:ss') - EXCEPTION: $_" | Out-File -FilePath (Join-Path $Root "git_push_log.txt") -Append -Encoding utf8
    } finally {
        Pop-Location
    }
}

# -- Helper: pick a valid MLB clean slate path ---------------------------------
function Resolve-MLBCleanSlateFile {
    param([string]$MLBDir)
    $candidates = @(
        (Join-Path $MLBDir "outputs\step8_mlb_direction_clean.xlsx"),
        (Join-Path $MLBDir "scripts\step8_mlb_direction_clean.xlsx"),
        (Join-Path $MLBDir "step8_mlb_direction_clean.xlsx")
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) {
            try {
                $len = (Get-Item $p).Length
                # Empty/placeholder workbooks are tiny (~5KB); require meaningful size.
                if ($len -ge 10240) { return $p }
            } catch { }
        }
    }
    return $null
}

# -- Helper: run combined, auto-detect all sports on disk ---------------------
function Run-Combined {
    param([string]$Reason = "")
    Write-Host ""
    $label = if ($Reason) { "[ COMBINED SLATE -- $Reason ]" } else { "[ COMBINED SLATE ]" }
    Write-Host $label -ForegroundColor Magenta
    Write-Host ""

    # Validate upstream sport outputs before building combined slate.
    $dqScript = Join-Path $Root "scripts\validate_pipeline_outputs.py"
    if (Test-Path $dqScript) {
        Write-Host "  [DQ] Validating upstream pipeline outputs..." -ForegroundColor Cyan
        $dqCmd = "py -3.14 `"$dqScript`" --date $Date --repo-root `"$Root`""
        if ($DQWarnOnly) { $dqCmd += " --warn-only" }
        $dqOut = Invoke-Expression "$dqCmd 2>&1"
        foreach ($line in $dqOut) { Write-Host "    $line" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0 -and -not $DQWarnOnly) {
            Write-Host "  [DQ] FAILED. Fix upstream pipeline data gaps before combine." -ForegroundColor Red
            return $false
        }
    } else {
        Write-Host "  [DQ] Validator script missing, skipping upstream data-quality gate." -ForegroundColor Yellow
    }

    # Clean up any stale root-level combined_slate_tickets files from previous runs
    Get-ChildItem -Path $Root -Filter "combined_slate_tickets_*.xlsx" | Remove-Item -Force -ErrorAction SilentlyContinue

    $nbaFile    = "$NBADir\data\outputs\step8_all_direction_clean.xlsx"
    $cbbFile    = "$CBBDir\step6_ranked_cbb.xlsx"
    $nhlFile    = "$NHLDir\step8_nhl_direction_clean.xlsx"
    $soccerFile = "$SoccerDir\outputs\step8_soccer_direction_clean.xlsx"
    $mlbFile    = Resolve-MLBCleanSlateFile -MLBDir $MLBDir
    $nba1hFile  = "$Root\NBA\step8_nba1h_direction_clean.xlsx"
    $nba1qFile  = "$Root\NBA\step8_nba1q_direction_clean.xlsx"
    $wcbbFile   = "$Root\CBB\step6_ranked_wcbb.xlsx"

    if (-not (Test-Path $nbaFile)) { Write-Host "  WARNING: NBA step8 not found -- skipping combined" -ForegroundColor Yellow; return $false }
    if (-not (Test-Path $cbbFile)) { Write-Host "  WARNING: CBB step6 not found -- skipping combined" -ForegroundColor Yellow; return $false }

    $CombinedOut  = Join-Path $Root "combined_slate_tickets_$Date.xlsx"
    $CombinedArgs  = "--nba `"$nbaFile`""
    $CombinedArgs += " --cbb `"$cbbFile`""

    if (Test-Path $nhlFile)    { $CombinedArgs += " --nhl `"$nhlFile`"";       Write-Host "  [+] NHL"    -ForegroundColor DarkGray }
    if (Test-Path $soccerFile) { $CombinedArgs += " --soccer `"$soccerFile`""; Write-Host "  [+] Soccer" -ForegroundColor DarkGray }
    if ($mlbFile)              { $CombinedArgs += " --mlb `"$mlbFile`"";       Write-Host "  [+] MLB"    -ForegroundColor DarkGray }
    if (Test-Path $nba1hFile)  { $CombinedArgs += " --nba1h `"$nba1hFile`"";   Write-Host "  [+] NBA1H"  -ForegroundColor DarkGray }
    if (Test-Path $nba1qFile)  { $CombinedArgs += " --nba1q `"$nba1qFile`"";   Write-Host "  [+] NBA1Q"  -ForegroundColor DarkGray }
    if (Test-Path $wcbbFile)   { $CombinedArgs += " --wcbb `"$wcbbFile`"";     Write-Host "  [+] WCBB"   -ForegroundColor DarkGray }

    $CombinedArgs += " --date $Date --output `"$CombinedOut`" --tiers A,B,C,D --max-tickets 3 --min-hit-rate 0.58 --write-web --web-outdir `"$WebOutDir`""

    Write-Host "`n[STEP 8] Building tickets..." -ForegroundColor Cyan
    Push-Location $Root
    try {
        $cmd = "py -3.14 .\scripts\combined_slate_tickets.py $CombinedArgs"
        $ticketOutput = Invoke-Expression "$cmd 2>&1"
        $ticketOutput | ForEach-Object { Write-Host $_ }
        $ticketOutput | Where-Object { $_ -match '^\[TICKETS\]' } | ForEach-Object {
            Write-Host $_ -ForegroundColor Green
        }
        $okC = ($LASTEXITCODE -eq 0)
    } finally {
        Pop-Location
    }

    # Retry once after a short wait if combined_slate_tickets.py failed (handles OneDrive file-lock race after pipeline)
    if (-not $okC) {
        Write-Host "  [RETRY] combined_slate_tickets failed — waiting 15s and retrying once..." -ForegroundColor Yellow
        Start-Sleep -Seconds 15
        Push-Location $Root
        try {
            $ticketOutput = Invoke-Expression "$cmd 2>&1"
            $ticketOutput | ForEach-Object { Write-Host $_ }
            $okC = ($LASTEXITCODE -eq 0)
        } finally {
            Pop-Location
        }
    }

    Write-Host "[STEP 8] Tickets complete." -ForegroundColor Cyan

    if ($okC) {
        Copy-Item $CombinedOut (Join-Path $OutDir "combined_slate_tickets_$Date.xlsx") -Force -ErrorAction SilentlyContinue
        Remove-Item $CombinedOut -Force -ErrorAction SilentlyContinue
        Write-Host "  Saved -> $(Join-Path $OutDir "combined_slate_tickets_$Date.xlsx")" -ForegroundColor Green

        # Save dated snapshot of tickets JSON for historical ticket eval (overwrite each run)
        $TicketsJson = Join-Path $Root "combined_slate_tickets_$Date.json"
        $LatestJson  = Join-Path $Root "ui_runner\templates\tickets_latest.json"
        if (Test-Path $LatestJson) {
            Copy-Item $LatestJson $TicketsJson -Force
            Write-Host "[INFO] Saved tickets snapshot: combined_slate_tickets_$Date.json" -ForegroundColor DarkGray
        }

        py -3.14 (Join-Path $Root "build_ticket_eval.py") --date $Date
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Ticket eval build failed (non-fatal) — combined slate was saved successfully"
        }
        Run-GitPush
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

# -- Helper: train prop ML models when primary .pkl is missing ----------------
function Ensure-PropModels {
    Write-Host "[ MODELS ] Checking prop_model_*.pkl (see models/README.md)..." -ForegroundColor Cyan
    $modelsDir = Join-Path $Root "models"
    if (-not (Test-Path $modelsDir)) { New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null }
    $trainers = @(
        @{ Pkl = "prop_model_nba.pkl";     Script = ".\scripts\train_prop_model_nba.py" },
        @{ Pkl = "prop_model_nba1q.pkl";  Script = ".\scripts\train_prop_model_nba1q.py" },
        @{ Pkl = "prop_model_nba1h.pkl";  Script = ".\scripts\train_prop_model_nba1h.py" },
        @{ Pkl = "prop_model_cbb.pkl";    Script = ".\scripts\train_prop_model_cbb.py" },
        @{ Pkl = "prop_model_nhl.pkl";    Script = ".\scripts\train_prop_model_nhl.py" },
        @{ Pkl = "prop_model_soccer.pkl"; Script = ".\scripts\train_prop_model_soccer.py" }
    )
    foreach ($t in $trainers) {
        $pklPath = Join-Path $modelsDir $t.Pkl
        if (-not (Test-Path $pklPath)) {
            Write-Host "  [MODELS] Missing $($t.Pkl) -> $($t.Script)" -ForegroundColor Yellow
            $okM = Run-Step "Train ML $($t.Pkl)" $Root $t.Script ""
            if (-not $okM) {
                Write-Host "  [MODELS] WARNING: training failed - ML blend may be skipped for this sport." -ForegroundColor Red
            }
        } else {
            Write-Host "  [MODELS] OK $($t.Pkl)" -ForegroundColor DarkGray
        }
    }
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
    & (Join-Path $Root "scripts\run_wnba_pipeline.ps1") -Date $Date
    Print-Done
    exit
}

# =============================================================================
#  NHL ONLY
# =============================================================================
if ($NHLOnly) {
    Write-Host "[ NHL PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    Ensure-PropModels
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"         "--output step1_nhl_props.csv" } } else { Write-Host "  [NHL] Skipping step1 fetch -- using existing step1_nhl_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input step1_nhl_props.csv --output step2_nhl_picktypes.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input step2_nhl_picktypes.csv --output step3_nhl_with_defense.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input step3_nhl_with_defense.csv --output step4_nhl_with_stats.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input step4_nhl_with_stats.csv --output step5_nhl_hit_rates.csv --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input step5_nhl_hit_rates.csv --output step6_nhl_role_context.csv" }
    if ($ok) { $ok = Run-Step "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input step6_nhl_role_context.csv --output step7_nhl_ranked.xlsx --slate-date $Date" }
    if ($ok) { $ok = Run-Step "NHL Step 8 - Direction Context"  $NHLDir ".\scripts\step8_add_direction_context_nhl.py"  "--input step7_nhl_ranked.xlsx --output step8_nhl_direction_clean.xlsx --date $Date" }
    if ($ok) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$NHLDir\step8_nhl_direction_clean.xlsx" "$OutDir\step8_nhl_direction_clean_$Date.xlsx" -Force
        Write-Host "  Archived NHL slate -> $OutDir\step8_nhl_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
    }
    Write-Host ""
    if ($ok) { Write-Host "  NHL complete." -ForegroundColor Green } else { Write-Host "  NHL FAILED." -ForegroundColor Red }
    if ($ok -and -not $SkipCombined) { Run-Combined "after NHL" }
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
    if (-not $SkipFetch) {
        if ($ok) { $ok = Run-Step "MLB Step 1 - Fetch PrizePicks" $MLBDir ".\scripts\step1_fetch_prizepicks_mlb.py" "--gentle --timeout 90 --output outputs\step1_mlb_props.csv" }
    } else { Write-Host "  [MLB] Skipping step1 fetch -- using existing outputs\step1_mlb_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "MLB Step 2 - Attach Pick Types"  $MLBDir ".\scripts\step2_attach_picktypes_mlb.py"       "--input outputs\step1_mlb_props.csv --output outputs\step2_mlb_picktypes.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 3 - Attach Defense"     $MLBDir ".\scripts\step3_attach_defense_mlb.py"         "--input outputs\step2_mlb_picktypes.csv --defense mlb_defense_summary.csv --output outputs\step3_mlb_with_defense.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 4 - Player Stats"       $MLBDir ".\scripts\step4_attach_player_stats_mlb.py"    "--input outputs\step3_mlb_with_defense.csv --cache outputs\mlb_stats_cache.csv --output outputs\step4_mlb_with_stats.csv --season 2025" }
    if ($ok) { $ok = Run-Step "MLB Step 5 - Line Hit Rates"     $MLBDir ".\scripts\step5_add_line_hit_rates_mlb.py"     "--input outputs\step4_mlb_with_stats.csv --output outputs\step5_mlb_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 6 - Team Role Context"  $MLBDir ".\scripts\step6_team_role_context_mlb.py"      "--input outputs\step5_mlb_hit_rates.csv --output outputs\step6_mlb_role_context.csv" }
    if ($ok) { $ok = Run-Step "MLB Step 7 - Rank Props"         $MLBDir ".\scripts\step7_rank_props_mlb.py"             "--input outputs\step6_mlb_role_context.csv --output outputs\step7_mlb_ranked.xlsx" }
    if ($ok) { $ok = Run-Step "MLB Step 8 - Direction Context"  $MLBDir ".\scripts\step8_add_direction_context_mlb.py"  "--input outputs\step7_mlb_ranked.xlsx --output outputs\step8_mlb_direction.csv --xlsx outputs\step8_mlb_direction_clean.xlsx" }
    if ($ok -and (Test-Path "$MLBDir\outputs\step8_mlb_direction_clean.xlsx")) {
        Copy-Item "$MLBDir\outputs\step8_mlb_direction_clean.xlsx" "$MLBDir\step8_mlb_direction_clean.xlsx" -Force
    }
    if ($ok -and (Test-Path "$MLBDir\step8_mlb_direction_clean.xlsx")) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$MLBDir\step8_mlb_direction_clean.xlsx" "$OutDir\step8_mlb_direction_clean_$Date.xlsx" -Force
        Write-Host "  Archived MLB slate -> $OutDir\step8_mlb_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
    } elseif (-not $ok) {
        # Fallback: if live MLB fetch is blocked (e.g. 403), publish last known-good slate for today's dated output.
        $mlbFallback = Resolve-MLBCleanSlateFile -MLBDir $MLBDir
        if ($mlbFallback) {
            if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
            Copy-Item $mlbFallback "$OutDir\step8_mlb_direction_clean_$Date.xlsx" -Force
            Write-Host "  MLB fallback snapshot used -> $OutDir\step8_mlb_direction_clean_$Date.xlsx" -ForegroundColor Yellow
            Write-Host "  Source: $mlbFallback" -ForegroundColor DarkGray
        }
    }
    Write-Host ""
    if ($ok) { Write-Host "  MLB complete." -ForegroundColor Green } else { Write-Host "  MLB FAILED." -ForegroundColor Red }
    if ($ok -and -not $SkipCombined) { Run-Combined "after MLB" }
    Print-Done
    exit
}

# =============================================================================
#  SOCCER ONLY
# =============================================================================
if ($SoccerOnly) {
    Write-Host "[ SOCCER PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    Ensure-PropModels
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output outputs\step1_soccer_props.csv" } } else { Write-Host "  [Soccer] Skipping step1 fetch -- using existing outputs\step1_soccer_props.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input outputs\step1_soccer_props.csv --output outputs\step2_soccer_picktypes.csv" }
    if ($ok) { $ok = Run-Step "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input outputs\step2_soccer_picktypes.csv --defense cache\soccer_defense_summary.csv --output outputs\step3_soccer_with_defense.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input outputs\step3_soccer_with_defense.csv --output outputs\step4_soccer_with_stats.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input outputs\step4_soccer_with_stats.csv --output outputs\step5_soccer_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input outputs\step5_soccer_hit_rates.csv --output outputs\step6_soccer_role_context.csv" }
    if ($ok) { $ok = Run-Step "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input outputs\step6_soccer_role_context.csv --output outputs\step7_soccer_ranked.xlsx --n_teams 15 --slate-date $Date" }
    if ($ok) { $ok = Run-Step "Soccer Step 8 - Direction Context"  $SoccerDir ".\scripts\step8_add_direction_context_soccer.py"  "--input outputs\step7_soccer_ranked.xlsx --sheet ALL --output outputs\step8_soccer_direction.csv --xlsx outputs\step8_soccer_direction_clean.xlsx --date $Date" }
    if ($ok) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$SoccerDir\outputs\step8_soccer_direction_clean.xlsx" "$OutDir\step8_soccer_direction_clean_$Date.xlsx" -Force
        Write-Host "  Archived Soccer slate -> $OutDir\step8_soccer_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
    }
    Write-Host ""
    if ($ok) { Write-Host "  Soccer complete." -ForegroundColor Green } else { Write-Host "  Soccer FAILED." -ForegroundColor Red }
    if ($ok -and -not $SkipCombined) { Run-Combined "after Soccer" }
    Print-Done
    exit
}

# =============================================================================
#  CBB ONLY
# =============================================================================
if ($CBBOnly) {
    Write-Host "[ CBB PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    Ensure-PropModels
    # CBB pipeline ends at step6 — no step7/step8 in this sport
    $CBBOutDir = Join-Path $CBBDir "outputs\$Date"
    if (-not (Test-Path $CBBOutDir)) { New-Item -ItemType Directory -Force -Path $CBBOutDir | Out-Null }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "CBB Step 1 - Fetch PrizePicks"      $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py"      "--out outputs\$Date\step1_cbb.csv" } } else { Write-Host "  [CBB] Skipping step1 fetch -- using existing outputs\$Date\step1_cbb.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input outputs\$Date\step1_cbb.csv --output outputs\$Date\step2_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input outputs\$Date\step2_cbb.csv --defense data\reference\cbb_def_rankings.csv --output outputs\$Date\step3b_with_def_rankings_cbb.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input outputs\$Date\step3b_with_def_rankings_cbb.csv --output outputs\$Date\step3_cbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input outputs\$Date\step3_cbb.csv --output outputs\$Date\step5b_cbb.csv --cache data\cache\cbb_boxscore_cache.csv --days 90 --workers 4" }
    if ($ok) { $ok = Run-Step "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input outputs\$Date\step5b_cbb.csv --output outputs\$Date\step6_ranked_cbb.xlsx --date $Date --cache data\cache\cbb_boxscore_cache.csv" }
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "WCBB Step 1 - Fetch PrizePicks"      $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py"      "--league_id 176 --out step1_wcbb.csv" } } else { Write-Host "  [WCBB] Skipping step1 fetch -- using existing step1_wcbb.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "WCBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input step1_wcbb.csv --output step2_wcbb.csv" }
    if ($ok) { $ok = Run-Step "WCBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input step2_wcbb.csv --defense data\reference\cbb_def_rankings.csv --output step3b_with_def_rankings_wcbb.csv" }
    if ($ok) { $ok = Run-Step "WCBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input step3b_with_def_rankings_wcbb.csv --output step3_wcbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step "WCBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input step3_wcbb.csv --output step5b_wcbb.csv --cache data\cache\wcbb_boxscore_cache.csv --days 21 --workers 4 --league womens-college-basketball" }
    if ($ok) { $ok = Run-Step "WCBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input step5b_wcbb.csv --output step6_ranked_wcbb.xlsx --date $Date --cache data\cache\wcbb_boxscore_cache.csv" }
    if ($ok) { Copy-Item "$CBBDir\outputs\$Date\step6_ranked_cbb.xlsx" "$CBBDir\step6_ranked_cbb.xlsx" -Force }
    if ($ok -and (Test-Path "$CBBDir\step6_ranked_wcbb.xlsx")) { Copy-Item "$CBBDir\step6_ranked_wcbb.xlsx" "$OutDir\step6_ranked_wcbb_$Date.xlsx" -Force -ErrorAction SilentlyContinue }
    if ($ok) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$CBBDir\outputs\$Date\step6_ranked_cbb.xlsx" "$OutDir\step6_ranked_cbb_$Date.xlsx" -Force -ErrorAction SilentlyContinue
        Write-Host "  Archived CBB slate -> $OutDir\step6_ranked_cbb_$Date.xlsx" -ForegroundColor DarkGray
    }
    Write-Host ""
    if ($ok) { Write-Host "  CBB complete." -ForegroundColor Green } else { Write-Host "  CBB FAILED." -ForegroundColor Red }
    if ($ok -and -not $SkipCombined) { Run-Combined "after CBB" }
    Print-Done
    exit
}

# =============================================================================
#  NBA ONLY
# =============================================================================
if ($NBAOnly) {
    Write-Host "[ NBA PIPELINE ]" -ForegroundColor Magenta
    Write-Host ""
    Ensure-PropModels

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
    # Build reference DB with yesterday's games (always runs so stats stay current)
    if ($ok) { $ok = Run-Step "NBA DB Build - Boxscore Ref"          $NBADir ".\scripts\build_boxscore_ref.py"                  "--days 1" }
    # Refresh defense rankings
    if ($ok) { $ok = Run-Step "NBA Defense Refresh"                   $NBADir ".\scripts\defense_report.py"                      "--season 2025-26 --out data\cache\defense_team_summary.csv" }
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step "NBA Step 1 - Fetch PrizePicks"    $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output data\outputs\step1_pp_props_today.csv" } } else { Write-Host "  [NBA] Skipping step1 fetch -- using existing data\outputs\step1_pp_props_today.csv" -ForegroundColor DarkGray }
    if ($ok) { $ok = Run-Step "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input data\outputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input data\outputs\step2_with_picktypes.csv --defense data\cache\defense_team_summary.csv --output data\outputs\step3_with_defense.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate data\outputs\step3_with_defense.csv --out data\outputs\step4_with_stats.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input data\outputs\step4_with_stats.csv --output data\outputs\step5_with_hit_rates.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input data\outputs\step5_with_hit_rates.csv --output data\outputs\step6_with_team_role_context.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input data\outputs\step6_with_team_role_context.csv --output data\outputs\step6a_with_opp_stats.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input data\outputs\step6a_with_opp_stats.csv --output data\outputs\step6b_with_game_context.csv --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input data\outputs\step6b_with_game_context.csv --output data\outputs\step6c_with_schedule_flags.csv --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input data\outputs\step6c_with_schedule_flags.csv --output data\outputs\step6d_with_h2h.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 6e - Intel Layer"            $NBADir ".\scripts\step6e_attach_intel.py"                 "--input data\outputs\step6d_with_h2h.csv --output data\outputs\step6e_with_intel.csv" }
    if ($ok) { $ok = Run-Step "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input data\outputs\step6e_with_intel.csv --output data\outputs\step7_ranked_props.xlsx --slate-date $Date" }
    if ($ok) { $ok = Run-Step "NBA Step 8 - Direction Context"       $NBADir ".\scripts\step8_add_direction_context.py"         "--input data\outputs\step7_ranked_props.xlsx --sheet ALL --output data\outputs\step8_all_direction.csv --date $Date" }

    if ($ok) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }
    if ($ok) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$NBADir\data\outputs\step8_all_direction_clean.xlsx" "$OutDir\step8_nba_direction_clean_$Date.xlsx" -Force
        Write-Host "  Archived NBA slate -> $OutDir\step8_nba_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
    }

    # -- NBA 1H (league_id=84) -----------------------------------------------
    Write-Host ""
    Write-Host "[ NBA 1H PIPELINE ]" -ForegroundColor Magenta
    $ok1h = $true
    if (-not $SkipFetch) { if ($ok1h) { $ok1h = Run-Step "NBA1H Step 1 - Fetch PrizePicks"  $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 84 --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output step1_nba1h_props.csv" } } else { Write-Host "  [NBA1H] Skipping step1 fetch" -ForegroundColor DarkGray }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 2 - Attach Pick Types"  $NBADir ".\scripts\step2_attach_picktypes.py"               "--input step1_nba1h_props.csv --output step2_nba1h_picktypes.csv" }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 3 - Attach Defense"     $NBADir ".\scripts\step3_attach_defense.py"                 "--input step2_nba1h_picktypes.csv --defense data\cache\defense_team_summary.csv --output step3_nba1h_with_defense.csv" }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 4 - Player Stats"       $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate step3_nba1h_with_defense.csv --out step4_nba1h_with_stats.csv" }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 5 - Line Hit Rates"     $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input step4_nba1h_with_stats.csv --output step5_nba1h_with_hit_rates.csv" }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 6 - Team Role Context"  $NBADir ".\scripts\step6_team_role_context.py"              "--input step5_nba1h_with_hit_rates.csv --output step6_nba1h_with_team_role_context.csv" }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 7 - Rank Props"         $NBADir ".\scripts\step7_rank_props.py"                    "--input step6_nba1h_with_team_role_context.csv --output step7_nba1h_ranked_props.xlsx --slate-date $Date" }
    if ($ok1h) { $ok1h = Run-Step "NBA1H Step 8 - Direction Context"  $NBADir ".\scripts\step8_add_direction_context.py"         "--input step7_nba1h_ranked_props.xlsx --sheet ALL --output step8_nba1h_direction.csv --date $Date" }
    if ($ok1h -and (Test-Path "$NBADir\step8_nba1h_direction_clean.xlsx")) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$NBADir\step8_nba1h_direction_clean.xlsx" "$OutDir\step8_nba1h_direction_clean_$Date.xlsx" -Force
        Write-Host "  Archived NBA1H slate -> $OutDir\step8_nba1h_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
    }
    if ($ok1h) { Write-Host "  NBA1H complete." -ForegroundColor Green } else { Write-Host "  NBA1H FAILED." -ForegroundColor Red }

    # -- NBA 1Q (league_id=192) ----------------------------------------------
    Write-Host ""
    Write-Host "[ NBA 1Q PIPELINE ]" -ForegroundColor Magenta
    $ok1q = $true
    if (-not $SkipFetch) { if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 1 - Fetch PrizePicks"  $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 192 --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output step1_nba1q_props.csv" } } else { Write-Host "  [NBA1Q] Skipping step1 fetch" -ForegroundColor DarkGray }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 2 - Attach Pick Types"  $NBADir ".\scripts\step2_attach_picktypes.py"               "--input step1_nba1q_props.csv --output step2_nba1q_picktypes.csv" }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 3 - Attach Defense"     $NBADir ".\scripts\step3_attach_defense.py"                 "--input step2_nba1q_picktypes.csv --defense data\cache\defense_team_summary.csv --output step3_nba1q_with_defense.csv" }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 4 - Player Stats"       $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate step3_nba1q_with_defense.csv --out step4_nba1q_with_stats.csv" }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 5 - Line Hit Rates"     $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input step4_nba1q_with_stats.csv --output step5_nba1q_with_hit_rates.csv" }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 6 - Team Role Context"  $NBADir ".\scripts\step6_team_role_context.py"              "--input step5_nba1q_with_hit_rates.csv --output step6_nba1q_with_team_role_context.csv" }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 7 - Rank Props"         $NBADir ".\scripts\step7_rank_props.py"                    "--input step6_nba1q_with_team_role_context.csv --output step7_nba1q_ranked_props.xlsx --slate-date $Date" }
    if ($ok1q) { $ok1q = Run-Step "NBA1Q Step 8 - Direction Context"  $NBADir ".\scripts\step8_add_direction_context.py"         "--input step7_nba1q_ranked_props.xlsx --sheet ALL --output step8_nba1q_direction.csv --date $Date" }
    if ($ok1q -and (Test-Path "$NBADir\step8_nba1q_direction_clean.xlsx")) {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        Copy-Item "$NBADir\step8_nba1q_direction_clean.xlsx" "$OutDir\step8_nba1q_direction_clean_$Date.xlsx" -Force
        Write-Host "  Archived NBA1Q slate -> $OutDir\step8_nba1q_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
    }
    if ($ok1q) { Write-Host "  NBA1Q complete." -ForegroundColor Green } else { Write-Host "  NBA1Q FAILED." -ForegroundColor Red }

    Write-Host ""
    if ($ok) { Write-Host "  NBA complete." -ForegroundColor Green } else { Write-Host "  NBA FAILED." -ForegroundColor Red }
    if ($ok -and -not $SkipCombined) { Run-Combined "after NBA" }
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

Ensure-PropModels

if (Test-Path (Join-Path $NBADir "RUN_COMPLETE.flag")) { Remove-Item (Join-Path $NBADir "RUN_COMPLETE.flag") -Force }

Write-Host "[ PARALLEL PIPELINE: NBA + NBA1H + NBA1Q + CBB + WCBB + NHL + Soccer + MLB ]" -ForegroundColor Magenta
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
    # Build reference DB with yesterday's games (always runs so stats stay current)
    if ($ok) { $ok = Run-Step-Job "NBA DB Build - Boxscore Ref"     $NBADir ".\scripts\build_boxscore_ref.py"                  "--days 1" }
    # Refresh defense rankings
    if ($ok) { $ok = Run-Step-Job "NBA Defense Refresh"              $NBADir ".\scripts\defense_report.py"                      "--season 2025-26 --out data\cache\defense_team_summary.csv" }
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NBA Step 1 - Fetch PrizePicks"    $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 7 --game_mode pickem --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output data\outputs\step1_pp_props_today.csv" } } else { Write-Output "[NBA] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 2 - Attach Pick Types"       $NBADir ".\scripts\step2_attach_picktypes.py"               "--input data\outputs\step1_pp_props_today.csv --output data\outputs\step2_with_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 3 - Attach Defense"          $NBADir ".\scripts\step3_attach_defense.py"                 "--input data\outputs\step2_with_picktypes.csv --defense data\cache\defense_team_summary.csv --output data\outputs\step3_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 4 - Player Stats (ESPN)"     $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate data\outputs\step3_with_defense.csv --out data\outputs\step4_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 5 - Line Hit Rates"          $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input data\outputs\step4_with_stats.csv --output data\outputs\step5_with_hit_rates.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6 - Team Role Context"       $NBADir ".\scripts\step6_team_role_context.py"              "--input data\outputs\step5_with_hit_rates.csv --output data\outputs\step6_with_team_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6a - Opponent H2H Stats"     $NBADir ".\scripts\step6a_attach_opponent_stats_NBA.py"     "--input data\outputs\step6_with_team_role_context.csv --output data\outputs\step6a_with_opp_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6b - Game Context (Vegas)"   $NBADir ".\scripts\step6b_attach_game_context.py"          "--input data\outputs\step6a_with_opp_stats.csv --output data\outputs\step6b_with_game_context.csv --api_key `"$OddsApiKey`" --date $Date --cache `"game_context_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6c - Schedule Flags (B2B)"   $NBADir ".\scripts\step6c_schedule_flags.py"               "--input data\outputs\step6b_with_game_context.csv --output data\outputs\step6c_with_schedule_flags.csv --date $Date --cache `"schedule_cache_$Date.csv`"" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6d - H2H Matchup Stats"      $NBADir ".\scripts\step6d_attach_h2h_matchups.py"          "--input data\outputs\step6c_with_schedule_flags.csv --output data\outputs\step6d_with_h2h.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 6e - Intel Layer"            $NBADir ".\scripts\step6e_attach_intel.py"                 "--input data\outputs\step6d_with_h2h.csv --output data\outputs\step6e_with_intel.csv" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 7 - Rank Props"              $NBADir ".\scripts\step7_rank_props.py"                    "--input data\outputs\step6e_with_intel.csv --output data\outputs\step7_ranked_props.xlsx --slate-date $Date" }
    if ($ok) { $ok = Run-Step-Job "NBA Step 8 - Direction Context"       $NBADir ".\scripts\step8_add_direction_context.py"         "--input data\outputs\step7_ranked_props.xlsx --sheet ALL --output data\outputs\step8_all_direction.csv --date $Date" }

    # -- NBA 1H (league_id=84) -----------------------------------------------
    Write-Output "[NBA1H] Starting period pipeline..."
    $ok1h = $true
    if (-not $SkipFetch) { if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 1 - Fetch PrizePicks"  $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 84 --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output step1_nba1h_props.csv" } } else { Write-Output "[NBA1H] Skipping step1 fetch" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 2 - Attach Pick Types"  $NBADir ".\scripts\step2_attach_picktypes.py"               "--input step1_nba1h_props.csv --output step2_nba1h_picktypes.csv" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 3 - Attach Defense"     $NBADir ".\scripts\step3_attach_defense.py"                 "--input step2_nba1h_picktypes.csv --defense data\cache\defense_team_summary.csv --output step3_nba1h_with_defense.csv" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 4 - Player Stats"       $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate step3_nba1h_with_defense.csv --out step4_nba1h_with_stats.csv" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 5 - Line Hit Rates"     $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input step4_nba1h_with_stats.csv --output step5_nba1h_with_hit_rates.csv" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 6 - Team Role Context"  $NBADir ".\scripts\step6_team_role_context.py"              "--input step5_nba1h_with_hit_rates.csv --output step6_nba1h_with_team_role_context.csv" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 7 - Rank Props"         $NBADir ".\scripts\step7_rank_props.py"                    "--input step6_nba1h_with_team_role_context.csv --output step7_nba1h_ranked_props.xlsx --slate-date $Date" }
    if ($ok1h) { $ok1h = Run-Step-Job "NBA1H Step 8 - Direction Context"  $NBADir ".\scripts\step8_add_direction_context.py"         "--input step7_nba1h_ranked_props.xlsx --sheet ALL --output step8_nba1h_direction.csv --date $Date" }
    if ($ok1h) { Write-Output "[NBA1H] complete." } else { Write-Output "[NBA1H] FAILED." }

    # -- NBA 1Q (league_id=192) ----------------------------------------------
    Write-Output "[NBA1Q] Starting period pipeline..."
    $ok1q = $true
    if (-not $SkipFetch) { if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 1 - Fetch PrizePicks"  $NBADir ".\scripts\step1_fetch_prizepicks_api.py"             "--league_id 192 --per_page 250 --max_pages 5 --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 --output step1_nba1q_props.csv" } } else { Write-Output "[NBA1Q] Skipping step1 fetch" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 2 - Attach Pick Types"  $NBADir ".\scripts\step2_attach_picktypes.py"               "--input step1_nba1q_props.csv --output step2_nba1q_picktypes.csv" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 3 - Attach Defense"     $NBADir ".\scripts\step3_attach_defense.py"                 "--input step2_nba1q_picktypes.csv --defense data\cache\defense_team_summary.csv --output step3_nba1q_with_defense.csv" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 4 - Player Stats"       $NBADir ".\scripts\step4_attach_player_stats_espn_cache.py" "--slate step3_nba1q_with_defense.csv --out step4_nba1q_with_stats.csv" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 5 - Line Hit Rates"     $NBADir ".\scripts\step5_add_line_hit_rates.py"             "--input step4_nba1q_with_stats.csv --output step5_nba1q_with_hit_rates.csv" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 6 - Team Role Context"  $NBADir ".\scripts\step6_team_role_context.py"              "--input step5_nba1q_with_hit_rates.csv --output step6_nba1q_with_team_role_context.csv" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 7 - Rank Props"         $NBADir ".\scripts\step7_rank_props.py"                    "--input step6_nba1q_with_team_role_context.csv --output step7_nba1q_ranked_props.xlsx --slate-date $Date" }
    if ($ok1q) { $ok1q = Run-Step-Job "NBA1Q Step 8 - Direction Context"  $NBADir ".\scripts\step8_add_direction_context.py"         "--input step7_nba1q_ranked_props.xlsx --sheet ALL --output step8_nba1q_direction.csv --date $Date" }
    if ($ok1q) { Write-Output "[NBA1Q] complete." } else { Write-Output "[NBA1Q] FAILED." }

    return $ok
} -ArgumentList $NBADir, $Date, $OddsApiKey, $SkipFetch

# -- CBB Job ------------------------------------------------------------------
$CBBJob = Start-Job -ScriptBlock {
    param($CBBDir, $SkipFetch, $Date)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
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
    $CBBOutDir = Join-Path $CBBDir "outputs\$Date"
    if (-not (Test-Path $CBBOutDir)) { New-Item -ItemType Directory -Force -Path $CBBOutDir | Out-Null }
    # CBB pipeline ends at step6 — no step7/step8 in this sport
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "CBB Step 1 - Fetch PrizePicks" $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py" "--out outputs\$Date\step1_cbb.csv" } } else { Write-Output "[CBB] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input outputs\$Date\step1_cbb.csv --output outputs\$Date\step2_cbb.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input outputs\$Date\step2_cbb.csv --defense data\reference\cbb_def_rankings.csv --output outputs\$Date\step3b_with_def_rankings_cbb.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input outputs\$Date\step3b_with_def_rankings_cbb.csv --output outputs\$Date\step3_cbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input outputs\$Date\step3_cbb.csv --output outputs\$Date\step5b_cbb.csv --cache data\cache\cbb_boxscore_cache.csv --days 90 --workers 4" }
    if ($ok) { $ok = Run-Step-Job "CBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input outputs\$Date\step5b_cbb.csv --output outputs\$Date\step6_ranked_cbb.xlsx --date $Date --cache data\cache\cbb_boxscore_cache.csv" }
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "WCBB Step 1 - Fetch PrizePicks" $CBBDir ".\scripts\pipeline\step1_pp_cbb_scraper.py" "--league_id 176 --out step1_wcbb.csv" } } else { Write-Output "[WCBB] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "WCBB Step 2 - Normalize"               $CBBDir ".\scripts\pipeline\step2_normalize.py"                            "--input step1_wcbb.csv --output step2_wcbb.csv" }
    if ($ok) { $ok = Run-Step-Job "WCBB Step 3 - Attach Defense Rankings" $CBBDir ".\scripts\pipeline\step3b_attach_def_rankings.py"                 "--input step2_wcbb.csv --defense data\reference\cbb_def_rankings.csv --output step3b_with_def_rankings_wcbb.csv" }
    if ($ok) { $ok = Run-Step-Job "WCBB Step 4 - Attach ESPN IDs"         $CBBDir ".\scripts\pipeline\step5a_attach_espn_ids.py"                     "--input step3b_with_def_rankings_wcbb.csv --output step3_wcbb.csv --master data/reference/ncaa_mbb_athletes_master.csv" }
    if ($ok) { $ok = Run-Step-Job "WCBB Step 5 - Boxscore Stats"          $CBBDir ".\scripts\pipeline\step5b_attach_boxscore_stats.py"               "--input step3_wcbb.csv --output step5b_wcbb.csv --cache data\cache\wcbb_boxscore_cache.csv --days 21 --workers 4 --league womens-college-basketball" }
    if ($ok) { $ok = Run-Step-Job "WCBB Step 6 - Rank Props"              $CBBDir ".\scripts\pipeline\step6_rank_props_cbb.py"                       "--input step5b_wcbb.csv --output step6_ranked_wcbb.xlsx --date $Date --cache data\cache\wcbb_boxscore_cache.csv" }
    if ($ok) { Copy-Item "$CBBDir\outputs\$Date\step6_ranked_cbb.xlsx" "$CBBDir\step6_ranked_cbb.xlsx" -Force }
    return $ok
} -ArgumentList $CBBDir, $SkipFetch, $Date

# -- NHL Job ------------------------------------------------------------------
$NHLJob = Start-Job -ScriptBlock {
    param($NHLDir, $SkipFetch, $Date)
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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "NHL Step 1 - Fetch PrizePicks" $NHLDir ".\scripts\step1_fetch_prizepicks_nhl.py"        "--output step1_nhl_props.csv" } } else { Write-Output "[NHL] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 2 - Attach Pick Types"  $NHLDir ".\scripts\step2_attach_picktypes_nhl.py"       "--input step1_nhl_props.csv --output step2_nhl_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 3 - Attach Defense"     $NHLDir ".\scripts\step3_attach_defense_nhl.py"         "--input step2_nhl_picktypes.csv --output step3_nhl_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 4 - Player Stats"       $NHLDir ".\scripts\step4_attach_player_stats_nhl.py"    "--input step3_nhl_with_defense.csv --output step4_nhl_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 5 - Line Hit Rates"     $NHLDir ".\scripts\step5_add_line_hit_rates_nhl.py"     "--input step4_nhl_with_stats.csv --output step5_nhl_hit_rates.csv --gamelog-cache cache\nhl_gamelog_cache.json" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 6 - Team Role Context"  $NHLDir ".\scripts\step6_team_role_context_nhl.py"      "--input step5_nhl_hit_rates.csv --output step6_nhl_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 7 - Rank Props"         $NHLDir ".\scripts\step7_rank_props_nhl.py"             "--input step6_nhl_role_context.csv --output step7_nhl_ranked.xlsx --slate-date $Date" }
    if ($ok) { $ok = Run-Step-Job "NHL Step 8 - Direction Context"  $NHLDir ".\scripts\step8_add_direction_context_nhl.py"  "--input step7_nhl_ranked.xlsx --output step8_nhl_direction_clean.xlsx --date $Date" }
    return $ok
} -ArgumentList $NHLDir, $SkipFetch, $Date

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
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "Soccer Step 1 - Fetch PrizePicks" $SoccerDir ".\scripts\step1_fetch_prizepicks_soccer.py" "--output outputs\step1_soccer_props.csv" } } else { Write-Output "[Soccer] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 2 - Attach Pick Types"  $SoccerDir ".\scripts\step2_attach_picktypes_soccer.py"       "--input outputs\step1_soccer_props.csv --output outputs\step2_soccer_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Defense Refresh"             $SoccerDir ".\scripts\soccer_defense_report.py"               "--out cache\soccer_defense_summary.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 3 - Attach Defense"     $SoccerDir ".\scripts\step3_attach_defense_soccer.py"         "--input outputs\step2_soccer_picktypes.csv --defense cache\soccer_defense_summary.csv --output outputs\step3_soccer_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 4 - Player Stats"       $SoccerDir ".\scripts\step4_attach_player_stats_soccer.py"    "--input outputs\step3_soccer_with_defense.csv --output outputs\step4_soccer_with_stats.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 5 - Line Hit Rates"     $SoccerDir ".\scripts\step5_add_line_hit_rates_soccer.py"     "--input outputs\step4_soccer_with_stats.csv --output outputs\step5_soccer_hit_rates.csv --compute10" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 6 - Team Role Context"  $SoccerDir ".\scripts\step6_team_role_context_soccer.py"      "--input outputs\step5_soccer_hit_rates.csv --output outputs\step6_soccer_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 7 - Rank Props"         $SoccerDir ".\scripts\step7_rank_props_soccer.py"             "--input outputs\step6_soccer_role_context.csv --output outputs\step7_soccer_ranked.xlsx --n_teams 15 --slate-date $Date" }
    if ($ok) { $ok = Run-Step-Job "Soccer Step 8 - Direction Context"  $SoccerDir ".\scripts\step8_add_direction_context_soccer.py"  "--input outputs\step7_soccer_ranked.xlsx --sheet ALL --output outputs\step8_soccer_direction.csv --xlsx outputs\step8_soccer_direction_clean.xlsx --date $Date" }
    return $ok
} -ArgumentList $SoccerDir, $Date, $SkipFetch

# -- MLB Job ------------------------------------------------------------------
$MLBJob = Start-Job -ScriptBlock {
    param($MLBDir, $Date, $SkipFetch)
    $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
    function Run-Step-Job {
        param([string]$Label,[string]$Dir,[string]$Script,[string]$Arguments="")
        Write-Output "[MLB] --> $Label"
        Push-Location $Dir
        try {
            $cmd = if ($Arguments) { "py -3.14 `"$Script`" $Arguments" } else { "py -3.14 `"$Script`"" }
            Write-Output "        CMD: $cmd"
            $output = Invoke-Expression $cmd 2>&1; $exit = $LASTEXITCODE
            foreach ($line in $output) { Write-Output "        $line" }
            if ($exit -ne 0) { Write-Output "[MLB] FAILED: $Label (exit $exit)"; return $false }
            Write-Output "[MLB] OK: $Label"; return $true
        } catch { Write-Output "[MLB] EXCEPTION: $_"; return $false
        } finally { Pop-Location }
    }
    $ok = $true
    if (-not $SkipFetch) { if ($ok) { $ok = Run-Step-Job "MLB Step 1 - Fetch PrizePicks" $MLBDir ".\scripts\step1_fetch_prizepicks_mlb.py" "--gentle --timeout 90 --retries 2 --output outputs\\step1_mlb_props.csv" } } else { Write-Output "[MLB] Skipping step1 fetch" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 2 - Attach Pick Types"  $MLBDir ".\scripts\step2_attach_picktypes_mlb.py"       "--input outputs\step1_mlb_props.csv --output outputs\step2_mlb_picktypes.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 3 - Attach Defense"     $MLBDir ".\scripts\step3_attach_defense_mlb.py"         "--input outputs\step2_mlb_picktypes.csv --defense mlb_defense_summary.csv --output outputs\step3_mlb_with_defense.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 4 - Player Stats"       $MLBDir ".\scripts\step4_attach_player_stats_mlb.py"    "--input outputs\step3_mlb_with_defense.csv --cache outputs\mlb_stats_cache.csv --output outputs\step4_mlb_with_stats.csv --season 2025" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 5 - Line Hit Rates"     $MLBDir ".\scripts\step5_add_line_hit_rates_mlb.py"     "--input outputs\step4_mlb_with_stats.csv --output outputs\step5_mlb_hit_rates.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 6 - Team Role Context"  $MLBDir ".\scripts\step6_team_role_context_mlb.py"      "--input outputs\step5_mlb_hit_rates.csv --output outputs\step6_mlb_role_context.csv" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 7 - Rank Props"         $MLBDir ".\scripts\step7_rank_props_mlb.py"             "--input outputs\step6_mlb_role_context.csv --output outputs\step7_mlb_ranked.xlsx" }
    if ($ok) { $ok = Run-Step-Job "MLB Step 8 - Direction Context"  $MLBDir ".\scripts\step8_add_direction_context_mlb.py"  "--input outputs\step7_mlb_ranked.xlsx --output outputs\step8_mlb_direction.csv --xlsx outputs\step8_mlb_direction_clean.xlsx" }
    if ($ok -and (Test-Path "$MLBDir\outputs\step8_mlb_direction_clean.xlsx")) {
        Copy-Item "$MLBDir\outputs\step8_mlb_direction_clean.xlsx" "$MLBDir\step8_mlb_direction_clean.xlsx" -Force
    }
    return $ok
} -ArgumentList $MLBDir, $Date, $SkipFetch

# -- Wait + stream output -----------------------------------------------------
$allJobs = @($NBAJob, $CBBJob, $NHLJob, $SoccerJob, $MLBJob)

Write-Host "  [Waiting for all pipelines to finish...]" -ForegroundColor DarkGray
Write-Host ""

while (($allJobs | Where-Object { $_.State -eq 'Running' }).Count -gt 0) {
    foreach ($job in $allJobs) { $out = Receive-Job $job -ErrorAction SilentlyContinue; foreach ($line in $out) { Write-Host "    $line" -ForegroundColor DarkGray } }
    Start-Sleep -Milliseconds 500
}
foreach ($job in $allJobs) { $out = Receive-Job $job -ErrorAction SilentlyContinue; foreach ($line in $out) { Write-Host "    $line" -ForegroundColor DarkGray } }

# -- Results ------------------------------------------------------------------
$NBASuccess    = Test-Path (Join-Path $NBADir    "data\outputs\step8_all_direction_clean.xlsx")
$CBBSuccess    = Test-Path (Join-Path $CBBDir    "step6_ranked_cbb.xlsx")
$NHLSuccess    = Test-Path (Join-Path $NHLDir    "step8_nhl_direction_clean.xlsx")
$SoccerSuccess = Test-Path (Join-Path $SoccerDir "outputs\step8_soccer_direction_clean.xlsx")
$MLBSuccess    = Test-Path (Join-Path $MLBDir    "step8_mlb_direction_clean.xlsx")

Remove-Job $allJobs -Force -ErrorAction SilentlyContinue
if ($NBASuccess) { New-Item -ItemType File -Force -Path (Join-Path $NBADir "RUN_COMPLETE.flag") | Out-Null }

# ── Archive dated slate copies for grader (must happen after jobs complete) ──
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
if ($NBASuccess) {
    Copy-Item "$NBADir\data\outputs\step8_all_direction_clean.xlsx" "$OutDir\step8_nba_direction_clean_$Date.xlsx" -Force
    Write-Host "  Archived NBA slate -> $OutDir\step8_nba_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}
if ($CBBSuccess) {
    Copy-Item "$CBBDir\outputs\$Date\step6_ranked_cbb.xlsx" "$OutDir\step6_ranked_cbb_$Date.xlsx" -Force -ErrorAction SilentlyContinue
    Write-Host "  Archived CBB slate -> $OutDir\step6_ranked_cbb_$Date.xlsx" -ForegroundColor DarkGray
}
if ($SoccerSuccess) {
    Copy-Item "$SoccerDir\outputs\step8_soccer_direction_clean.xlsx" "$OutDir\step8_soccer_direction_clean_$Date.xlsx" -Force
    Write-Host "  Archived Soccer slate -> $OutDir\step8_soccer_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}
if ($NHLSuccess) {
    Copy-Item "$NHLDir\step8_nhl_direction_clean.xlsx" "$OutDir\step8_nhl_direction_clean_$Date.xlsx" -Force
    Write-Host "  Archived NHL slate -> $OutDir\step8_nhl_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}
 $mlbArchiveSource = Resolve-MLBCleanSlateFile -MLBDir $MLBDir
if ($mlbArchiveSource) {
    Copy-Item $mlbArchiveSource "$OutDir\step8_mlb_direction_clean_$Date.xlsx" -Force
    Write-Host "  Archived MLB slate -> $OutDir\step8_mlb_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}
if (Test-Path "$Root\NBA\step8_nba1h_direction_clean.xlsx") {
    Copy-Item "$Root\NBA\step8_nba1h_direction_clean.xlsx" "$OutDir\step8_nba1h_direction_clean_$Date.xlsx" -Force
    Write-Host "  Archived NBA1H slate -> $OutDir\step8_nba1h_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}
if (Test-Path "$Root\NBA\step8_nba1q_direction_clean.xlsx") {
    Copy-Item "$Root\NBA\step8_nba1q_direction_clean.xlsx" "$OutDir\step8_nba1q_direction_clean_$Date.xlsx" -Force
    Write-Host "  Archived NBA1Q slate -> $OutDir\step8_nba1q_direction_clean_$Date.xlsx" -ForegroundColor DarkGray
}
if (Test-Path "$Root\CBB\step6_ranked_wcbb.xlsx") {
    Copy-Item "$Root\CBB\step6_ranked_wcbb.xlsx" "$OutDir\step6_ranked_wcbb_$Date.xlsx" -Force
    Write-Host "  Archived WCBB slate -> $OutDir\step6_ranked_wcbb_$Date.xlsx" -ForegroundColor DarkGray
}

Write-Host ""
@(
    @{ Name="NBA";    Ok=$NBASuccess },
    @{ Name="NBA1H";  Ok=(Test-Path "$Root\NBA\step8_nba1h_direction_clean.xlsx") },
    @{ Name="NBA1Q";  Ok=(Test-Path "$Root\NBA\step8_nba1q_direction_clean.xlsx") },
    @{ Name="CBB";    Ok=$CBBSuccess },
    @{ Name="WCBB";   Ok=(Test-Path "$Root\CBB\step6_ranked_wcbb.xlsx") },
    @{ Name="NHL";    Ok=$NHLSuccess },
    @{ Name="Soccer"; Ok=$SoccerSuccess },
    @{ Name="MLB";    Ok=$MLBSuccess }
) | ForEach-Object {
    if ($_.Ok) { Write-Host "  $($_.Name) complete." -ForegroundColor Green }
    else        { Write-Host "  $($_.Name) FAILED."  -ForegroundColor Red   }
}

if (-not $SkipCombined) { Run-Combined "full parallel run" }
Print-Done

