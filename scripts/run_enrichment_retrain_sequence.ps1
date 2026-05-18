# Background-friendly: pipelines -> build_retrain -> verify (train only if verify exits 0)
# Requires PowerShell 7+ (run_pipeline.ps1 #requires -Version 7.2).
# Usage: pwsh -File scripts/run_enrichment_retrain_sequence.ps1 -Date 2026-05-18
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$SkipTrain
)

# Re-launch under pwsh when started from Windows PowerShell 5.x
if ($PSVersionTable.PSVersion.Major -lt 7) {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwsh) {
        Write-Error "PowerShell 7+ required. Install pwsh or run: pwsh -File $PSCommandPath -Date $Date"
        exit 1
    }
    $argList = @("-NoProfile", "-File", $PSCommandPath, "-Date", $Date)
    if ($SkipTrain) { $argList += "-SkipTrain" }
    & $pwsh.Source @argList
    exit $LASTEXITCODE
}

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$log = Join-Path $Root "outputs\enrichment_retrain_run_$Date.log"
function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $log -Value $line
    Write-Host $line
}

Log "=== enrichment retrain sequence start (Date=$Date) ==="

Log "--- smoke test ---"
py -3.14 scripts/verify_enrichment_ready.py --smoke-test 2>&1 | ForEach-Object { Log $_ }

foreach ($sport in @("NBA", "WNBA", "MLB", "NHL")) {
    Log "--- pipeline $sport ---"
    & .\run_pipeline.ps1 -Sport $sport -SkipFetch -Date $Date 2>&1 | ForEach-Object { Log $_ }
    if ($LASTEXITCODE -ne 0) { Log "WARN: $sport pipeline exit $LASTEXITCODE" }
}

Log "--- build_retrain_dataset ---"
py -3.14 scripts/build_retrain_dataset.py 2>&1 | ForEach-Object { Log $_ }

Log "--- verify enrichment ready ---"
py -3.14 scripts/verify_enrichment_ready.py 2>&1 | ForEach-Object { Log $_ }
$verifyExit = $LASTEXITCODE

if (-not $SkipTrain -and $verifyExit -eq 0) {
    Log "--- train_edge_model ---"
    py -3.14 scripts/train_edge_model.py `
        --input-csv data/retrain_dataset.csv `
        --temporal-split `
        --output-model models/edge_model_unified.pkl 2>&1 | ForEach-Object { Log $_ }
} elseif ($verifyExit -ne 0) {
    Log "SKIP train: verify exit $verifyExit"
}

Log "=== done (verify exit $verifyExit) log=$log ==="
exit $verifyExit
