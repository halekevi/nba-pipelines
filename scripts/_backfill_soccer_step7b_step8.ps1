# Backfill Soccer step7b + step8 for dates missing blended_score (post May-1 wiring bug).
$ErrorActionPreference = "Continue"
$Root = "H:\halek\ProfileFromC\Desktop\PropORACLE"
$SoccerDir = Join-Path $Root "Sports\Soccer"
$Step7b = Join-Path $Root "scripts\step7b_edge_score.py"
$Step8 = Join-Path $SoccerDir "scripts\step8_add_direction_context_soccer.py"

$dates = @(
    "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05", "2026-05-06",
    "2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11",
    "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15", "2026-05-16",
    "2026-05-17", "2026-05-18", "2026-05-19", "2026-05-20"
)

foreach ($d in $dates) {
    $outDir = Join-Path $Root "outputs\$d\soccer"
    $step7 = Join-Path $outDir "step7_soccer_ranked.xlsx"
    if (-not (Test-Path $step7)) {
        Write-Host "[$d] step7 missing - skip" -ForegroundColor Yellow
        continue
    }

    $step8Csv = Join-Path $outDir "step8_soccer_direction.csv"
    $step8XlsxSoccer = Join-Path $outDir "step8_soccer_direction_clean.xlsx"
    $step8Dated = Join-Path $Root "outputs\$d\step8_soccer_direction_clean_$d.xlsx"

    Write-Host "[$d] step7b..." -ForegroundColor Cyan
    & py -3.14 $Step7b --sport Soccer --step7-xlsx $step7 --repo-root $Root 2>&1 |
        Select-String -Pattern "Scored|WARN|ERROR|error"

    Write-Host "[$d] step8..." -ForegroundColor Cyan
    & py -3.14 $Step8 `
        --input $step7 `
        --sheet ALL `
        --output $step8Csv `
        --xlsx $step8XlsxSoccer `
        --date $d 2>&1 |
        Select-String -Pattern "Kept|Saved|ERROR|error|counts"

    if (Test-Path $step8XlsxSoccer) {
        Copy-Item -LiteralPath $step8XlsxSoccer -Destination $step8Dated -Force
        Write-Host "[$d] copied -> $step8Dated" -ForegroundColor DarkGray
    }
}

Write-Host "Done." -ForegroundColor Green
