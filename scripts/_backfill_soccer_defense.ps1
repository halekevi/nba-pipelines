# Backfill Soccer defense context: step3-8 for dated soccer/ folders, enrich legacy parent step8 xlsx.
$ErrorActionPreference = "Continue"
$Root = "H:\halek\ProfileFromC\Desktop\PropORACLE"
$SoccerDir = Join-Path $Root "Sports\Soccer"
$SoccerScripts = Join-Path $SoccerDir "scripts"
$CacheDir = Join-Path $SoccerDir "cache"
$Step7b = Join-Path $Root "scripts\step7b_edge_score.py"
$Step7 = Join-Path $SoccerScripts "step7_rank_props_soccer.py"
$Step8 = Join-Path $SoccerScripts "step8_add_direction_context_soccer.py"
$Enrich = Join-Path $Root "scripts\enrich_soccer_step8_defense.py"
$DefenseCsv = Join-Path $CacheDir "soccer_defense_summary.csv"

Write-Host "[Soccer] Defense cache refresh (DB is primary; CSV fallback)..." -ForegroundColor Cyan
& py -3.14 (Join-Path $SoccerScripts "soccer_defense_report.py") --out $DefenseCsv 2>&1 |
    Select-String -Pattern "Saved|ERROR|rows|teams"

# --- Part A: full step3-8 for outputs/<date>/soccer with step2 ---
$datedDirs = Get-ChildItem (Join-Path $Root "outputs") -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' }
foreach ($dir in $datedDirs) {
    $d = $dir.Name
    $outDir = Join-Path $dir.FullName "soccer"
    $step2 = Join-Path $outDir "step2_soccer_picktypes.csv"
    if (-not (Test-Path $step2)) { continue }

    Write-Host "[$d] step3-8 (dated soccer/)..." -ForegroundColor Cyan
    $s3 = Join-Path $outDir "step3_soccer_with_defense.csv"
    $s4 = Join-Path $outDir "step4_soccer_with_stats.csv"
    $s5 = Join-Path $outDir "step5_soccer_hit_rates.csv"
    $s6 = Join-Path $outDir "step6_soccer_role_context.csv"
    $s7 = Join-Path $outDir "step7_soccer_ranked.xlsx"
    $s8Csv = Join-Path $outDir "step8_soccer_direction.csv"
    $s8Xlsx = Join-Path $outDir "step8_soccer_direction_clean.xlsx"
    $s8Dated = Join-Path $dir.FullName "step8_soccer_direction_clean_$d.xlsx"

    & py -3.14 (Join-Path $SoccerScripts "step3_attach_defense_soccer.py") `
        --input $step2 --defense $DefenseCsv --output $s3 2>&1 |
        Select-String -Pattern "def_tier|Defense filled|ERROR"
    if ($LASTEXITCODE -ne 0) { Write-Host "[$d] step3 FAILED" -ForegroundColor Red; continue }

    & py -3.14 (Join-Path $SoccerScripts "step4_attach_player_stats_soccer.py") `
        --input $s3 --cache (Join-Path $CacheDir "soccer_stats_cache.csv") --output $s4 --workers 6 2>&1 |
        Select-String -Pattern "Saved|ERROR|rows"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 (Join-Path $SoccerScripts "step5_add_line_hit_rates_soccer.py") `
        --input $s4 --output $s5 --compute10 2>&1 |
        Select-String -Pattern "Saved|ERROR|Wrote"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 (Join-Path $SoccerScripts "step6_team_role_context_soccer.py") `
        --input $s5 --output $s6 2>&1 |
        Select-String -Pattern "Saved|ERROR"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 $Step7 --input $s6 --output $s7 --n_teams 15 2>&1 |
        Select-String -Pattern "Saved|ERROR|Wrote|def_tier"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 $Step7b --sport Soccer --step7-xlsx $s7 --repo-root $Root 2>&1 |
        Select-String -Pattern "Scored|WARN|ERROR"
    & py -3.14 $Step8 --input $s7 --sheet ALL --output $s8Csv --xlsx $s8Xlsx --date $d 2>&1 |
        Select-String -Pattern "Kept|Saved|ERROR|Def Tier"
    if (Test-Path $s8Xlsx) {
        Copy-Item -LiteralPath $s8Xlsx -Destination $s8Dated -Force
    }
}

# --- Part B: enrich legacy parent step8 (Mar/Apr slates without soccer/ subfolder) ---
foreach ($dir in $datedDirs) {
    $d = $dir.Name
    $parent = Join-Path $dir.FullName "step8_soccer_direction_clean_$d.xlsx"
    if (-not (Test-Path $parent)) { continue }
    $socDir = Join-Path $dir.FullName "soccer"
    if (-not (Test-Path $socDir)) { New-Item -ItemType Directory -Force -Path $socDir | Out-Null }
    $enriched = Join-Path $socDir "step8_soccer_direction_clean.xlsx"
    & py -3.14 $Enrich --input $parent --output $enriched 2>&1
    if (Test-Path $enriched) {
        Copy-Item -LiteralPath $enriched -Destination $parent -Force
    }
}

Write-Host "[Soccer] Backfill complete." -ForegroundColor Green
