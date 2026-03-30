param(
    [switch]$Execute
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Root = Split-Path -Parent $ScriptDir

function Move-ToDateFolder {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DateText
    )
    $destDir = Join-Path $Root ("outputs\" + $DateText)
    $destPath = Join-Path $destDir (Split-Path -Leaf $SourcePath)

    Write-Host "  [MOVE] $(Split-Path -Leaf $SourcePath) -> outputs\$DateText\" -ForegroundColor Yellow
    if (-not $Execute) { return }

    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Move-Item -Path $SourcePath -Destination $destPath -Force
}

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
if ($Execute) {
    Write-Host "  ORGANIZE MODE (apply changes)" -ForegroundColor Yellow
} else {
    Write-Host "  PREVIEW MODE (no file moves)" -ForegroundColor Cyan
}
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

# Move root-level combined ticket artifacts to outputs/YYYY-MM-DD/
Get-ChildItem -Path $Root -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^combined_slate_tickets_(\d{4}-\d{2}-\d{2})\.(xlsx|json)$' } |
    ForEach-Object {
        $dateText = $Matches[1]
        Move-ToDateFolder -SourcePath $_.FullName -DateText $dateText
    }

# Move root-level performance summaries to outputs/YYYY-MM-DD/performance/
Get-ChildItem -Path $Root -File -Filter "MyTicketPerformance_summary_*.csv" -ErrorAction SilentlyContinue |
    ForEach-Object {
        if ($_.Name -match '^MyTicketPerformance_summary_(\d{4})(\d{2})(\d{2})_\d{6}\.csv$') {
            $dateText = "$($Matches[1])-$($Matches[2])-$($Matches[3])"
            $destDir = Join-Path $Root ("outputs\" + $dateText + "\performance")
            $destPath = Join-Path $destDir $_.Name
            Write-Host "  [MOVE] $($_.Name) -> outputs\$dateText\performance\" -ForegroundColor Yellow
            if ($Execute) {
                if (-not (Test-Path $destDir)) {
                    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
                }
                Move-Item -Path $_.FullName -Destination $destPath -Force
            }
        }
    }

# Move dated debug reports to logs/
Get-ChildItem -Path $Root -File -Filter "grade_debug_*.txt" -ErrorAction SilentlyContinue |
    ForEach-Object {
        $destDir = Join-Path $Root "logs"
        $destPath = Join-Path $destDir $_.Name
        Write-Host "  [MOVE] $($_.Name) -> logs\" -ForegroundColor Yellow
        if ($Execute) {
            if (-not (Test-Path $destDir)) {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }
            Move-Item -Path $_.FullName -Destination $destPath -Force
        }
    }

Write-Host ""
if ($Execute) {
    Write-Host "Done. Root cleanup applied." -ForegroundColor Green
} else {
    Write-Host "Preview complete. Re-run with -Execute to apply." -ForegroundColor Cyan
}
Write-Host ""
