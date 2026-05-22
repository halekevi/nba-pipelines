# Backfill NHL pipeline context: step3-8 (+ step4b) for dated outputs/<date>/nhl/ folders.
$ErrorActionPreference = "Continue"
$Root = "H:\halek\ProfileFromC\Desktop\PropORACLE"
$NHLDir = Join-Path $Root "Sports\NHL"
$NHLScripts = Join-Path $NHLDir "scripts"
$CacheDir = Join-Path $NHLDir "cache"
$Step7b = Join-Path $Root "scripts\step7b_edge_score.py"
$Step7Script = Join-Path $NHLScripts "step7_rank_props_nhl.py"
$Step8Script = Join-Path $NHLScripts "step8_add_direction_context_nhl.py"
$DefenseCsv = Join-Path $CacheDir "nhl_defense_summary.csv"
$GamelogCache = Join-Path $CacheDir "nhl_gamelog_cache.json"

# No archived step1-7 for these slates (graded_nhl fallback in build_retrain_dataset).
$skipNoPipeline = @("2026-04-09", "2026-04-10")

Write-Host "[NHL] Defense cache refresh..." -ForegroundColor Cyan
$defReport = Join-Path $NHLScripts "nhl_defense_report.py"
if (Test-Path $defReport) {
    & py -3.14 $defReport --out $DefenseCsv 2>&1 |
        Select-String -Pattern "Saved|ERROR|rows|teams|Wrote"
}

$datedDirs = Get-ChildItem (Join-Path $Root "outputs") -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' }
foreach ($dir in $datedDirs) {
    $d = $dir.Name
    if ($skipNoPipeline -contains $d) {
        Write-Host "[$d] SKIP — no pipeline archive (graded_nhl fallback)" -ForegroundColor Yellow
        continue
    }
    $outDir = Join-Path $dir.FullName "nhl"
    $step2 = Join-Path $outDir "step2_nhl_picktypes.csv"
    if (-not (Test-Path $step2)) { continue }

    Write-Host "[$d] step3-8 (dated nhl/)..." -ForegroundColor Cyan
    $s3 = Join-Path $outDir "step3_nhl_with_defense.csv"
    $s4 = Join-Path $outDir "step4_nhl_with_stats.csv"
    $s5 = Join-Path $outDir "step5_nhl_hit_rates.csv"
    $s6 = Join-Path $outDir "step6_nhl_role_context.csv"
    $step7Out = Join-Path $outDir "step7_nhl_ranked.xlsx"
    $s8Xlsx = Join-Path $outDir "step8_nhl_direction_clean.xlsx"
    $s8Csv = Join-Path $outDir "step8_nhl_direction_clean.csv"
    $s8Dated = Join-Path $dir.FullName "step8_nhl_direction_clean_$d.xlsx"

    $s3Args = @("--input", $step2, "--output", $s3)
    if (Test-Path $DefenseCsv) { $s3Args += @("--defense", $DefenseCsv) }
    & py -3.14 (Join-Path $NHLScripts "step3_attach_defense_nhl.py") @s3Args 2>&1 |
        Select-String -Pattern "def_tier|Defense|ERROR|Saved|filled"
    if ($LASTEXITCODE -ne 0) { Write-Host "[$d] step3 FAILED" -ForegroundColor Red; continue }

    & py -3.14 (Join-Path $NHLScripts "step4_attach_player_stats_nhl.py") `
        --input $s3 --output $s4 2>&1 |
        Select-String -Pattern "Saved|ERROR|rows"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 (Join-Path $NHLScripts "step4b_attach_nst_context_nhl.py") `
        --input $s4 --output $s4 2>&1 |
        Select-String -Pattern "Saved|ERROR|pp_toi|NST|WARN"
    if ($LASTEXITCODE -ne 0) { Write-Host "[$d] step4b WARN (continuing)" -ForegroundColor Yellow }

    $s5Args = @("--input", $s4, "--output", $s5)
    if (Test-Path $GamelogCache) { $s5Args += @("--gamelog-cache", $GamelogCache) }
    & py -3.14 (Join-Path $NHLScripts "step5_add_line_hit_rates_nhl.py") @s5Args 2>&1 |
        Select-String -Pattern "Saved|ERROR|Wrote"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 (Join-Path $NHLScripts "step6_team_role_context_nhl.py") `
        --input $s5 --output $s6 2>&1 |
        Select-String -Pattern "Saved|ERROR"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 $Step7Script --input $s6 --output $step7Out 2>&1 |
        Select-String -Pattern "Saved|ERROR|Wrote|def_tier"
    if ($LASTEXITCODE -ne 0) { continue }

    & py -3.14 $Step7b --sport NHL --step7-xlsx $step7Out --repo-root $Root 2>&1 |
        Select-String -Pattern "Scored|WARN|ERROR"
    & py -3.14 $Step8Script --input $step7Out --output $s8Xlsx --date $d 2>&1 |
        Select-String -Pattern "Dated copy|Saved|ERROR|XLSX"
    if (Test-Path $s8Xlsx) {
        Copy-Item -LiteralPath $s8Xlsx -Destination $s8Dated -Force
        & py -3.14 -c @"
import pandas as pd
from pathlib import Path
df = pd.read_excel(Path(r'$s8Xlsx'), engine='openpyxl')
df.to_csv(Path(r'$s8Csv'), index=False, encoding='utf-8-sig')
print(f'CSV {len(df):,} rows -> {r'$s8Csv'}')
"@
    }
}

# --- Part B: step7b+8 only when step7 exists but step2 missing (partial archives) ---
foreach ($dir in $datedDirs) {
    $d = $dir.Name
    if ($skipNoPipeline -contains $d) { continue }
    $nhlDir = Join-Path $dir.FullName "nhl"
    $step7Out = Join-Path $nhlDir "step7_nhl_ranked.xlsx"
    $step2 = Join-Path $nhlDir "step2_nhl_picktypes.csv"
    if (-not (Test-Path $step7Out) -or (Test-Path $step2)) { continue }

    Write-Host "[$d] step7b+8 only (no step2)..." -ForegroundColor Cyan
    $step8Xlsx = Join-Path $nhlDir "step8_nhl_direction_clean.xlsx"
    $step8Csv = Join-Path $nhlDir "step8_nhl_direction_clean.csv"
    $step8Dated = Join-Path $dir.FullName "step8_nhl_direction_clean_$d.xlsx"
    & py -3.14 $Step7b --sport NHL --step7-xlsx $step7Out --repo-root $Root 2>&1 |
        Select-String -Pattern "Scored|WARN|ERROR"
    & py -3.14 $Step8Script --input $step7Out --output $step8Xlsx --date $d 2>&1 |
        Select-String -Pattern "Dated copy|Saved|ERROR"
    if (Test-Path $step8Xlsx) {
        Copy-Item -LiteralPath $step8Xlsx -Destination $step8Dated -Force
        & py -3.14 -c "import pandas as pd; df=pd.read_excel(r'$step8Xlsx',engine='openpyxl'); df.to_csv(r'$step8Csv',index=False,encoding='utf-8-sig'); print(len(df))"
    }
}

Write-Host "[NHL] Backfill complete." -ForegroundColor Green
