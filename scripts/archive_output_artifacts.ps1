param(
    [string]$Root = (Split-Path $PSScriptRoot -Parent),
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$outputsRoot = Join-Path $Root "outputs"
if (-not (Test-Path $outputsRoot)) {
    Write-Host "No outputs folder found at: $outputsRoot" -ForegroundColor Yellow
    exit 0
}

$dateDirs = Get-ChildItem -Path $outputsRoot -Directory | Where-Object { $_.Name -match "^\d{4}-\d{2}-\d{2}$" }
if (-not $dateDirs) {
    Write-Host "No date-based output directories found." -ForegroundColor Yellow
    exit 0
}

$movedCount = 0
foreach ($dir in $dateDirs) {
    $canonicalDir = Join-Path $dir.FullName "canonical"
    $runsDir = Join-Path $dir.FullName "runs"
    if (-not (Test-Path $runsDir) -and $Apply) {
        New-Item -ItemType Directory -Path $runsDir -Force | Out-Null
    }

    $targets = Get-ChildItem -Path $dir.FullName -File -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -like "combined_slate_tickets_*.xlsx" -or $_.Name -like "combined_slate_tickets_*.json") -and
        ($_.Name -notlike "*_to_grade_tomorrow.xlsx")
    }

    foreach ($file in $targets) {
        # Skip if this exact file exists in canonical (already source-of-truth copy).
        $canonicalPeer = Join-Path $canonicalDir $file.Name
        if (Test-Path $canonicalPeer) {
            if ($Apply) {
                $dest = Join-Path $runsDir $file.Name
                Move-Item -Path $file.FullName -Destination $dest -Force
                Write-Host "Moved: $($file.FullName) -> $dest" -ForegroundColor Green
            } else {
                Write-Host "[DRY-RUN] Would move: $($file.FullName) -> $(Join-Path $runsDir $file.Name)" -ForegroundColor Cyan
            }
            $movedCount++
        }
    }
}

if ($Apply) {
    Write-Host "Archive complete. Files moved: $movedCount" -ForegroundColor Green
} else {
    Write-Host "Dry run complete. Files that would be moved: $movedCount" -ForegroundColor Yellow
    Write-Host "Re-run with -Apply to perform moves." -ForegroundColor Yellow
}
