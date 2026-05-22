# Regenerate corrupt outputs/2026-05-21/nhl/step7+step8 from step6.
$ErrorActionPreference = "Continue"
$Root = "H:\halek\ProfileFromC\Desktop\PropORACLE"
$d = "2026-05-21"
$nhlDir = Join-Path $Root "outputs\$d\nhl"
$step6 = Join-Path $nhlDir "step6_nhl_role_context.csv"
$step7Out = Join-Path $nhlDir "step7_nhl_ranked.xlsx"
$step8Xlsx = Join-Path $nhlDir "step8_nhl_direction_clean.xlsx"
$step8Csv = Join-Path $nhlDir "step8_nhl_direction_clean.csv"
$step8Dated = Join-Path $Root "outputs\$d\step8_nhl_direction_clean_$d.xlsx"
$NHLScripts = Join-Path $Root "Sports\NHL\scripts"
$Step7b = Join-Path $Root "scripts\step7b_edge_score.py"
$Step7Script = Join-Path $NHLScripts "step7_rank_props_nhl.py"
$Step8Script = Join-Path $NHLScripts "step8_add_direction_context_nhl.py"

if (-not (Test-Path $step6)) {
    Write-Host "[ERROR] Missing $step6 — run _backfill_nhl_defense.ps1 for full rebuild" -ForegroundColor Red
    exit 1
}

foreach ($f in @($step7Out, $step8Xlsx)) {
    if (Test-Path $f) {
        $bak = "$f.bak_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item -LiteralPath $f -Destination $bak -Force
        Write-Host "Backed up -> $bak" -ForegroundColor DarkGray
    }
}

Write-Host "[$d] step7 from step6..." -ForegroundColor Cyan
& py -3.14 $Step7Script --input $step6 --output $step7Out
if ($LASTEXITCODE -ne 0) { Write-Host "step7 FAILED" -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host "[$d] step7b..." -ForegroundColor Cyan
& py -3.14 $Step7b --sport NHL --step7-xlsx $step7Out --repo-root $Root
if ($LASTEXITCODE -ne 0) { Write-Host "step7b FAILED" -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host "[$d] step8..." -ForegroundColor Cyan
& py -3.14 $Step8Script --input $step7Out --output $step8Xlsx --date $d
if ($LASTEXITCODE -ne 0) { Write-Host "step8 FAILED" -ForegroundColor Red; exit $LASTEXITCODE }

if (Test-Path $step8Xlsx) {
    Copy-Item -LiteralPath $step8Xlsx -Destination $step8Dated -Force
    & py -3.14 -c @"
import pandas as pd
from pathlib import Path
df = pd.read_excel(Path(r'$step8Xlsx'), engine='openpyxl')
df.to_csv(Path(r'$step8Csv'), index=False, encoding='utf-8-sig')
print(f'OK: {len(df):,} rows xlsx+csv')
"@
    Write-Host "[$d] Done." -ForegroundColor Green
} else {
    Write-Host "[ERROR] step8 xlsx not created" -ForegroundColor Red
    exit 1
}
