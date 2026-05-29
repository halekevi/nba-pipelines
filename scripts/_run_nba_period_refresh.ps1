# Refresh NBA1H + NBA1Q step1-8 for a slate date (no combined/tickets).
#
# -Date must match PrizePicks filtered_game_dates (America/New_York). Late playoff
#   games often list as the *next* calendar day vs your local run date — if step1
#   prints "0 rows", re-run with that board date (e.g. -Date 2026-05-30).
#
# After this script:
#   py -3 scripts/build_matchup_edge_json.py --sport nba1h
#   py -3 scripts/build_matchup_edge_json.py --sport nba1q
#   (one sport per invocation — "nba1h nba1q" is not valid)
#   py -3 scripts/generate_mobile_bundle.py
#
# See docs/NBA_PERIOD_SLATE_REFRESH.md
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$NBADir = Join-Path $Root "Sports\NBA"
$OutDir = Join-Path $Root "outputs\$Date"
$SportsRoot = Join-Path $Root "Sports"

foreach ($t in @("nba1h", "nba1q")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $OutDir $t) | Out-Null
}

function Run-StepLocal {
    param(
        [string]$Label,
        [string]$Script,
        [string[]]$StepArgs
    )
    Write-Host "  --> $Label" -ForegroundColor Yellow
    Push-Location $NBADir
    try {
        & py -3 $Script @StepArgs
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed (exit $LASTEXITCODE)"
        }
    } finally {
        Pop-Location
    }
}

function Run-NBAPeriodOnly {
    param(
        [string]$Tag,
        [string]$LeagueId
    )
    $tagLower = $Tag.ToLower()
    $periodOutDir = Join-Path $OutDir $tagLower
    $step1 = Join-Path $periodOutDir "step1_${tagLower}_props.csv"
    $step2 = Join-Path $periodOutDir "step2_${tagLower}_picktypes.csv"
    $step3 = Join-Path $periodOutDir "step3_${tagLower}_with_defense.csv"
    $step4 = Join-Path $periodOutDir "step4_${tagLower}_with_stats.csv"
    $step5 = Join-Path $periodOutDir "step5_${tagLower}_with_hit_rates.csv"
    $step6 = Join-Path $periodOutDir "step6_${tagLower}_with_team_role_context.csv"
    $step7 = Join-Path $periodOutDir "step7_${tagLower}_ranked_props.xlsx"
    $step8Csv = Join-Path $periodOutDir "step8_${tagLower}_direction.csv"
    $step8Xlsx = Join-Path $periodOutDir "step8_${tagLower}_direction_clean.xlsx"
    $step8Script = Join-Path $SportsRoot "NBA\scripts\step8_add_direction_context.py"

    Write-Host ""
    Write-Host "[ NBA PERIOD: $tagLower ]" -ForegroundColor Magenta

    Push-Location $NBADir
    try {
        & py -3 ".\scripts\step1_fetch_prizepicks_api.py" `
            --league_id $LeagueId --game_mode pickem --per_page 250 --max_pages 5 `
            --sleep 2.0 --cooldown_seconds 90 --max_cooldowns 3 --jitter_seconds 10.0 `
            --replace --output $step1 --date $Date
        if ($LASTEXITCODE -ne 0) { throw "step1 failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }

    Run-StepLocal "step2" ".\scripts\step2_attach_picktypes.py" @("--input", $step1, "--output", $step2)
    Run-StepLocal "step3" ".\scripts\step3_attach_defense.py" @(
        "--input", $step2, "--defense", "data\cache\defense_team_summary.csv", "--output", $step3
    )
    Run-StepLocal "step4" ".\scripts\step4_attach_player_stats_espn_cache.py" @(
        "--slate", $step3, "--out", $step4, "--date", $Date
    )
    if ($tagLower -eq "nba1h") {
        Run-StepLocal "step4b" ".\scripts\step4b_attach_nba_context.py" @(
            "--input", $step4, "--output", $step4, "--season", "2025-26"
        )
        Run-StepLocal "step4e" ".\scripts\step4e_attach_nba1h_context.py" @(
            "--input", $step4, "--output", $step4
        )
    }
    Run-StepLocal "step5" ".\scripts\step5_add_line_hit_rates.py" @(
        "--input", $step4, "--output", $step5, "--compute10"
    )
    Run-StepLocal "step6" ".\scripts\step6_team_role_context.py" @("--input", $step5, "--output", $step6)
    Run-StepLocal "step7" ".\scripts\step7_rank_props.py" @("--input", $step6, "--output", $step7)
    Run-StepLocal "step8" $step8Script @(
        "--input", $step7, "--sheet", "ALL", "--output", $step8Csv, "--xlsx", $step8Xlsx, "--date", $Date
    )

    $legacy = Join-Path $NBADir "step8_${tagLower}_direction_clean.xlsx"
    Copy-Item -LiteralPath $step8Xlsx -Destination $legacy -Force
    Write-Host "  OK $tagLower -> $step8Xlsx" -ForegroundColor Green
}

Run-NBAPeriodOnly -Tag "nba1h" -LeagueId "84"
Run-NBAPeriodOnly -Tag "nba1q" -LeagueId "192"

foreach ($tag in @("nba1h", "nba1q")) {
    $step8 = Join-Path $OutDir "$tag\step8_${tag}_direction.csv"
    Write-Host "  --> Matchup edge JSON ($tag)" -ForegroundColor Yellow
    Push-Location $Root
    try {
        $meArgs = @(".\scripts\build_matchup_edge_json.py", "--sport", $tag)
        if (Test-Path -LiteralPath $step8) {
            $meArgs += @("--slate", $step8)
        }
        & py -3 @meArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      matchup-edge WARN ($tag exit $LASTEXITCODE)" -ForegroundColor Yellow
        } else {
            Write-Host "      OK" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
}
