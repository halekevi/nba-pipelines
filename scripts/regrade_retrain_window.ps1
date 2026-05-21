<#
.SYNOPSIS
  Re-run run_grader.ps1 for every file_date in data/retrain_dataset.csv (post NBA join fix).

.DESCRIPTION
  Reads unique file_date values from the retrain CSV, optionally filters -From/-To,
  skips dates without outputs\<date>\actuals_nba_<date>.csv, logs pass/fail per date,
  and prints a summary. Use -RunRebuild after a clean pass to rebuild retrain CSV.

.EXAMPLE
  cd H:\halek\ProfileFromC\Desktop\PropORACLE
  .\scripts\regrade_retrain_window.ps1

.EXAMPLE
  .\scripts\regrade_retrain_window.ps1 -From 2026-05-02 -DryRun

.EXAMPLE
  .\scripts\regrade_retrain_window.ps1 -RunRebuild
#>
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$RetrainCsv = "",
    [string]$From = "",
    [string]$To = "",
    [switch]$DryRun,
    [switch]$RunRebuild,
    [switch]$RequireStep8,
    [string]$LogDir = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $RepoRoot).Path
if (-not $RetrainCsv) {
    $RetrainCsv = Join-Path $Root "data\retrain_dataset.csv"
}
$GraderScript = Join-Path $Root "scripts\run_grader.ps1"
$RebuildScript = Join-Path $Root "scripts\build_retrain_dataset.py"
$Py = "py"
$PyVer = "-3.14"

if (-not $LogDir) {
    $LogDir = Join-Path $Root "logs"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$masterLog = Join-Path $LogDir "regrade_window_$stamp.log"

function Write-Log {
    param([string]$Message, [string]$Color = "")
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -LiteralPath $masterLog -Value $line -Encoding UTF8
    if ($Color) { Write-Host $line -ForegroundColor $Color }
    else { Write-Host $line }
}

function Get-RetrainDates {
    param([string]$CsvPath)
    $cmd = @"
import pandas as pd
from pathlib import Path
p = Path(r'$($CsvPath -replace "'", "''")')
df = pd.read_csv(p, low_memory=False)
col = 'file_date' if 'file_date' in df.columns else None
if not col:
    for c in df.columns:
        if 'date' in str(c).lower():
            col = c
            break
if not col:
    raise SystemExit('no file_date column in ' + str(p))
dates = sorted(df[col].dropna().astype(str).str[:10].unique().tolist())
for d in dates:
    print(d)
"@
    $lines = & $Py $PyVer -c $cmd 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Failed to read dates from $CsvPath`: $lines" }
    return @($lines | Where-Object { $_ -match '^\d{4}-\d{2}-\d{2}$' })
}

function Test-DatePrereqs {
    param([string]$Date)
    $dateDir = Join-Path $Root "outputs\$Date"
    $actuals = Join-Path $dateDir "actuals_nba_$Date.csv"
    $step8 = Join-Path $dateDir "step8_nba_direction_clean_$Date.xlsx"
    [pscustomobject]@{
        Date = $Date
        HasActuals = (Test-Path -LiteralPath $actuals)
        ActualsPath = $actuals
        HasStep8 = (Test-Path -LiteralPath $step8)
        Step8Path = $step8
    }
}

Write-Log "=== Re-grade retrain window (NBA join fix) ===" "Cyan"
Write-Log "Repo: $Root"
Write-Log "Retrain CSV: $RetrainCsv"
Write-Log "Master log: $masterLog"

if (-not (Test-Path -LiteralPath $RetrainCsv)) {
    throw "Retrain CSV not found: $RetrainCsv"
}
if (-not (Test-Path -LiteralPath $GraderScript)) {
    throw "run_grader.ps1 not found: $GraderScript"
}

$allDates = Get-RetrainDates -CsvPath $RetrainCsv
$dates = @($allDates)
if ($From) { $dates = @($dates | Where-Object { $_ -ge $From }) }
if ($To) { $dates = @($dates | Where-Object { $_ -le $To }) }

Write-Log "Total dates in CSV: $($allDates.Count); window to grade: $($dates.Count) ($($dates[0]) .. $($dates[-1]))"

$results = New-Object System.Collections.Generic.List[object]
$skipped = 0
$passed = 0
$failed = 0

foreach ($d in $dates) {
    $pre = Test-DatePrereqs -Date $d
    if (-not $pre.HasActuals) {
        Write-Log "SKIP $d — missing actuals: $($pre.ActualsPath)" "Yellow"
        $results.Add([pscustomobject]@{ Date = $d; Status = "SKIP_NO_ACTUALS"; ExitCode = $null; Log = "" })
        $skipped++
        continue
    }
    if ($RequireStep8 -and -not $pre.HasStep8) {
        Write-Log "SKIP $d — missing step8: $($pre.Step8Path)" "Yellow"
        $results.Add([pscustomobject]@{ Date = $d; Status = "SKIP_NO_STEP8"; ExitCode = $null; Log = "" })
        $skipped++
        continue
    }

    $dateLog = Join-Path $LogDir "regrade_$d`_$stamp.log"
    if ($DryRun) {
        Write-Log "DRY-RUN would grade: $d (actuals OK$(if ($pre.HasStep8) { ', step8 OK' } else { '' }))" "DarkGray"
        $results.Add([pscustomobject]@{ Date = $d; Status = "DRY_RUN"; ExitCode = 0; Log = $dateLog })
        continue
    }

    Write-Log "GRADE $d ..." "Green"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $GraderScript -Date $d *>&1 | Tee-Object -FilePath $dateLog | Out-Host
        $code = $LASTEXITCODE
    } catch {
        $code = 1
        $_ | Out-String | Add-Content -LiteralPath $dateLog
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($code -eq 0) {
        Write-Log "OK   $d (log: $dateLog)" "Green"
        $results.Add([pscustomobject]@{ Date = $d; Status = "OK"; ExitCode = 0; Log = $dateLog })
        $passed++
    } else {
        Write-Log "FAIL $d exit=$code (log: $dateLog)" "Red"
        $results.Add([pscustomobject]@{ Date = $d; Status = "FAIL"; ExitCode = $code; Log = $dateLog })
        $failed++
    }
}

Write-Log "" 
Write-Log "=== Summary ===" "Cyan"
Write-Log "  Graded OK:  $passed"
Write-Log "  Failed:     $failed"
Write-Log "  Skipped:    $skipped"
Write-Log "  Dry-run:    $DryRun"

$summaryCsv = Join-Path $LogDir "regrade_window_summary_$stamp.csv"
$results | Export-Csv -LiteralPath $summaryCsv -NoTypeInformation -Encoding UTF8
Write-Log "Summary CSV: $summaryCsv"

if ($failed -gt 0) {
    Write-Log "One or more dates failed — fix logs before rebuild." "Red"
    exit 1
}

if ($RunRebuild) {
    if ($DryRun) {
        Write-Log "DRY-RUN: would run build_retrain_dataset.py" "DarkGray"
    } else {
        $rebuildLog = Join-Path $LogDir "rebuild_post_gradingfix_$stamp.log"
        Write-Log "Running build_retrain_dataset.py -> $rebuildLog" "Cyan"
        & $Py $PyVer $RebuildScript --verbose 2>&1 | Tee-Object -FilePath $rebuildLog
        if ($LASTEXITCODE -ne 0) {
            Write-Log "build_retrain_dataset.py failed (exit $LASTEXITCODE)" "Red"
            exit $LASTEXITCODE
        }
        Write-Log "Rebuild complete. Next: train_edge_model.py on clean labels." "Green"
    }
} else {
    Write-Log "Next step (after reviewing logs):" "Cyan"
    Write-Log "  py -3.14 scripts/build_retrain_dataset.py --verbose"
    Write-Log "  py -3.14 scripts/train_edge_model.py --input-csv data/retrain_dataset.csv --temporal-split --temporal-date-column file_date"
}

exit 0
