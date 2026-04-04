param(
  [switch]$NBAOnly,
  [switch]$CBBOnly,
  [switch]$CombinedOnly,
  [switch]$SkipCombined
)

$ErrorActionPreference = "Stop"

$StartTime = Get-Date
$Date = Get-Date -Format "yyyy-MM-dd"
$Root = (Get-Location).Path
$OutDir = Join-Path $Root ("outputs\" + $Date)

# ---- Directories ----
$NBADir = Join-Path $Root "NbaPropPipelineA"
$CBBDir = Join-Path $Root "CBB2"

# ---- Ensure outputs dir ----
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Run-Step {
  param(
    [Parameter(Mandatory=$true)][string]$Title,
    [Parameter(Mandatory=$true)][string]$WorkDir,
    [Parameter(Mandatory=$true)][string]$Cmd
  )
  Write-Host "  --> $Title" -ForegroundColor Yellow
  Push-Location $WorkDir
  try {
    Invoke-Expression $Cmd
    if ($LASTEXITCODE -ne 0) { throw "Exit code $LASTEXITCODE" }
    Write-Host "      OK" -ForegroundColor Green
  } finally {
    Pop-Location
  }
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ("  PROP PIPELINE MASTER RUN  |  " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")) -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host ("Output folder: " + $OutDir) -ForegroundColor DarkGray
Write-Host ""

# ---- Mode flags ----
$RunNBA = (-not $CBBOnly) -and (-not $CombinedOnly)
$RunCBB = (-not $NBAOnly) -and (-not $CombinedOnly)

$NBASuccess = $false
$CBBSuccess = $false

# =========================
#          NBA
# =========================
if ($RunNBA) {
  if (-not (Test-Path $NBADir)) {
    Write-Host "[ NBA ] Skipped — folder not found: $NBADir" -ForegroundColor DarkGray
    Write-Host ""
  } else {
    Write-Host "[ NBA - PipelineA ]" -ForegroundColor Magenta
    try {
      Run-Step "Step 1 - Fetch PrizePicks"        $NBADir "py -3.14 .\step1_fetch_prizepicks_api.py --league_id 7 --game_mode pickem --per_page 150"
      Run-Step "Step 2 - Attach PickTypes/IDs"    $NBADir "py -3.14 .\step2_attach_picktypes.py --input step1_fetch_prizepicks_api.csv --output step2_attach_picktypes.csv"
      Run-Step "Step 3 - Attach Defense"          $NBADir "py -3.14 .\step3_attach_defense.py --input step2_attach_picktypes.csv --defense defense_team_summary.csv --output step3_with_defense.csv"
      Run-Step "Step 4 - Attach Player Stats"     $NBADir "py -3.14 .\step4_attach_player_stats.py --input step3_with_defense.csv --output step4_with_stats.csv --season 2025-26 --cache-dir ./_nba_cache"
      Run-Step "Step 5 - Line Hit Rates"          $NBADir "py -3.14 .\step5_add_line_hit_rates.py --input step4_with_stats.csv --output step5_with_line_hit_rates.csv"
      Run-Step "Step 6 - Team/Role Context"       $NBADir "py -3.14 .\step6_team_role_context.py --input step5_with_line_hit_rates.csv --output step6_with_team_role_context.csv"
      Run-Step "Step 7 - Rank Props"              $NBADir "py -3.14 .\step7_rank_props.py --input step6_with_team_role_context.csv --output step7_ranked_props.xlsx"
      Run-Step "Step 8 - Direction Context"       $NBADir "py -3.14 .\step8_add_direction_context.py --input step7_ranked_props.xlsx --sheet ALL --output step8_all_direction.csv"

      Copy-Item "$NBADir\step7_ranked_props.xlsx" "$OutDir\nba_ranked_$Date.xlsx" -Force -ErrorAction SilentlyContinue
      Copy-Item "$NBADir\step8_all_direction.csv" "$OutDir\nba_direction_$Date.csv" -Force -ErrorAction SilentlyContinue

      $NBASuccess = $true
      Write-Host ""
      Write-Host "  NBA done. Outputs saved to $OutDir" -ForegroundColor Green
    } catch {
      Write-Host "  NBA FAILED: $_" -ForegroundColor Red
    }
    Write-Host ""
  }
}

# =========================
#          CBB
# =========================
if ($RunCBB) {
  if (-not (Test-Path $CBBDir)) {
    Write-Host "[ CBB ] Skipped — folder not found: $CBBDir" -ForegroundColor DarkGray
    Write-Host ""
  } else {
    Write-Host "[ CBB ]" -ForegroundColor Magenta
    try {
      Run-Step "Step 1 - Fetch PrizePicks (CBB)"  $CBBDir "py -3.14 .\pp_cbb_scraper.py --league_id 20 --per_page 250 --single_stat --game_mode prizepools --out step1_cbb.csv"
      Run-Step "Step 2 - Normalize"               $CBBDir "py -3.14 .\cbb_step2_normalize.py --input step1_cbb.csv --output step2_cbb.csv"
      Run-Step "Step 3b - Attach Def Rankings"    $CBBDir "py -3.14 .\cbb_step3b_attach_def_rankings.py --input step2_cbb.csv --output step3b_with_def_rankings_cbb.csv --save_rankings cbb_def_rankings.csv"
      Run-Step "Step 5b - Attach Boxscore Stats"  $CBBDir "py -3.14 .\cbb_step5b_attach_boxscore_stats.py --input step3b_with_def_rankings_cbb.csv --output step5b_cbb.csv"
      Run-Step "Step 6 - Rank Props"              $CBBDir "py -3.14 .\cbb_step6_rank_props.py --input step5b_cbb.csv --output step6_ranked_cbb.xlsx"
      Run-Step "Step 7 - Build Tickets"           $CBBDir "py -3.14 .\cbb_step7_build_tickets.py --input step6_ranked_cbb.xlsx --output cbb_tickets_$Date.xlsx"

      Copy-Item "$CBBDir\step6_ranked_cbb.xlsx"  "$OutDir\cbb_ranked_$Date.xlsx"   -Force -ErrorAction SilentlyContinue
      Copy-Item "$CBBDir\cbb_tickets_$Date.xlsx" "$OutDir\cbb_tickets_$Date.xlsx"  -Force -ErrorAction SilentlyContinue

      $CBBSuccess = $true
      Write-Host ""
      Write-Host "  CBB done. Outputs saved to $OutDir" -ForegroundColor Green
    } catch {
      Write-Host "  CBB FAILED: $_" -ForegroundColor Red
    }
    Write-Host ""
  }
}

# =========================
#        COMBINED
# =========================
$RunCombined = (-not $SkipCombined) -and (-not $NBAOnly) -and (-not $CBBOnly)

if ($CombinedOnly) {
  $RunCombined = $true
  $NBASuccess = Test-Path (Join-Path $NBADir "step7_ranked_props.xlsx")
  $CBBSuccess = Test-Path (Join-Path $CBBDir "step6_ranked_cbb.xlsx")
}

if ($RunCombined -and ($NBASuccess -or $CBBSuccess)) {
  Write-Host "[ COMBINED ]" -ForegroundColor Magenta
  Write-Host ""
  $CombinedScript = Join-Path $Root "combined_slate_tickets.py"
  if (Test-Path $CombinedScript) {
    try {
      $nbaFile = Join-Path $NBADir "step7_ranked_props.xlsx"
      $cbbFile = Join-Path $CBBDir "step6_ranked_cbb.xlsx"
      $outFile = Join-Path $OutDir ("combined_slate_tickets_" + $Date + ".xlsx")

      Run-Step "Combined Slate + Tickets" $Root ("py -3.14 `"$CombinedScript`" --nba `"$nbaFile`" --cbb `"$cbbFile`" --date $Date --output `"$outFile`" --tiers A,B --max-tickets 20")
      Write-Host "  Combined done. Saved to $outFile" -ForegroundColor Green
    } catch {
      Write-Host "  COMBINED FAILED: $_" -ForegroundColor Red
    }
  } else {
    Write-Host "  Combined skipped — combined_slate_tickets.py not found at root." -ForegroundColor DarkGray
  }
  Write-Host ""
} elseif ($RunCombined) {
  Write-Host "[ COMBINED ] Skipped — no successful NBA/CBB output available." -ForegroundColor DarkGray
  Write-Host ""
}

$Elapsed = (Get-Date) - $StartTime
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ("  DONE  |  " + $Elapsed.ToString("mm\:ss") + "  |  " + $OutDir) -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
