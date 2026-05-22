# Backfill NHL step7b + step8 for dated outputs/<date>/nhl/ slates (and copy parent dated xlsx).
# Superseded by _backfill_nhl_defense.ps1 (step3-8 + step4b + legacy step7-only path).
& (Join-Path $PSScriptRoot "_backfill_nhl_defense.ps1")
exit $LASTEXITCODE

# --- legacy inline backfill (kept for reference) ---
$ErrorActionPreference = "Continue"
$Root = "H:\halek\ProfileFromC\Desktop\PropORACLE"
$NHLScripts = Join-Path $Root "Sports\NHL\scripts"
$Step7b = Join-Path $Root "scripts\step7b_edge_score.py"
$Step8 = Join-Path $NHLScripts "step8_add_direction_context_nhl.py"

# Apr 9-10 lack pipeline inputs; graded_nhl fallback in build_retrain_dataset handles those slates.
$skipNoStep7 = @("2026-04-09", "2026-04-10")

$datedDirs = Get-ChildItem (Join-Path $Root "outputs") -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' }
foreach ($dir in $datedDirs) {
    $d = $dir.Name
    if ($skipNoStep7 -contains $d) {
        Write-Host "[$d] SKIP — no archived step7 (use graded_nhl join fallback)" -ForegroundColor Yellow
        continue
    }
    $nhlDir = Join-Path $dir.FullName "nhl"
    $step7 = Join-Path $nhlDir "step7_nhl_ranked.xlsx"
    if (-not (Test-Path $step7)) { continue }

    $step8Xlsx = Join-Path $nhlDir "step8_nhl_direction_clean.xlsx"
    $step8Csv = Join-Path $nhlDir "step8_nhl_direction_clean.csv"
    $step8Dated = Join-Path $dir.FullName "step8_nhl_direction_clean_$d.xlsx"

    Write-Host "[$d] NHL step7b + step8..." -ForegroundColor Cyan
    & py -3.14 $Step7b --sport NHL --step7-xlsx $step7 --repo-root $Root 2>&1 |
        Select-String -Pattern "Scored|WARN|ERROR"
    & py -3.14 $Step8 --input $step7 --output $step8Xlsx --date $d 2>&1 |
        Select-String -Pattern "Dated copy|Saved|ERROR|rows"
    if (Test-Path $step8Xlsx) {
        Copy-Item -LiteralPath $step8Xlsx -Destination $step8Dated -Force
        # Export CSV for build_retrain_dataset (prefers nhl/step8 csv with rank_score).
        & py -3.14 -c "
import pandas as pd
from pathlib import Path
p = Path(r'$step8Xlsx')
df = pd.read_excel(p, engine='openpyxl')
out = Path(r'$step8Csv')
df.to_csv(out, index=False, encoding='utf-8-sig')
print(f'CSV {len(df):,} rows -> {out}')
" 2>&1
    }
}

Write-Host "[NHL] Backfill complete." -ForegroundColor Green
