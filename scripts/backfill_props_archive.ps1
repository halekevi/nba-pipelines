#requires -Version 7.2
<#
.SYNOPSIS
  Replay step_archive.py for every graded workbook under outputs/<Date>/ (Prop Evaluation data source).

.DESCRIPTION
  Writes rows into data/cache/*_props_history.db for the given grade date. Use when:
  - You graded slates but skipped the end of run_grader.ps1, or
  - You added new archive sports (CBB/WCBB/NBA1H/NBA1Q) and want older dates populated.

.EXAMPLE
  pwsh -File scripts\backfill_props_archive.ps1 -Date 2026-04-04

.EXAMPLE
  pwsh -File scripts\backfill_props_archive.ps1 -ScanOutputsDays 14
#>
param(
    [string]$Date = "",
    [int]$ScanOutputsDays = 0,
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = if ($RepoRoot.Trim()) { $RepoRoot.Trim() } else { Split-Path $PSScriptRoot -Parent }
$ArchiveScript = Join-Path $Root "scripts\step_archive.py"
if (-not (Test-Path $ArchiveScript)) {
    Write-Error "Missing $ArchiveScript"
}

$pairs = @(
    @{ Sport = "NBA";    Pattern = "graded_nba_{0}.xlsx" },
    @{ Sport = "CBB";    Pattern = "graded_cbb_{0}.xlsx" },
    @{ Sport = "WCBB";   Pattern = "graded_wcbb_{0}.xlsx" },
    @{ Sport = "NBA1H";  Pattern = "graded_nba1h_{0}.xlsx" },
    @{ Sport = "NBA1Q";  Pattern = "graded_nba1q_{0}.xlsx" },
    @{ Sport = "NHL";    Pattern = "graded_nhl_{0}.xlsx" },
    @{ Sport = "MLB";    Pattern = "graded_mlb_{0}.xlsx" },
    @{ Sport = "Soccer"; Pattern = "graded_soccer_{0}.xlsx" }
)

function Invoke-ArchiveForDate([string]$d) {
    $outDir = Join-Path $Root "outputs\$d"
    if (-not (Test-Path $outDir)) {
        Write-Warning "No folder outputs\$d — skip"
        return
    }
    $any = $false
    foreach ($p in $pairs) {
        $name = $p.Pattern -f $d
        $path = Join-Path $outDir $name
        if (-not (Test-Path $path)) { continue }
        $any = $true
        Write-Host "[$d] $($p.Sport) <- $name" -ForegroundColor Cyan
        & py -3.14 -X utf8 $ArchiveScript --sport $p.Sport --graded $path --date $d
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "step_archive exit $LASTEXITCODE for $($p.Sport)"
        }
    }
    if (-not $any) {
        Write-Host "[$d] No graded_*.xlsx found — nothing to archive" -ForegroundColor DarkYellow
    }
}

if ($ScanOutputsDays -gt 0) {
    $outRoot = Join-Path $Root "outputs"
    if (-not (Test-Path $outRoot)) {
        Write-Warning "No outputs folder"
        exit 0
    }
    $cutoff = [datetime]::UtcNow.Date.AddDays(-$ScanOutputsDays)
    Get-ChildItem -Path $outRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $leaf = $_.Name
        if ($leaf -notmatch '^\d{4}-\d{2}-\d{2}$') { return }
        try {
            $dt = [datetime]::ParseExact($leaf, "yyyy-MM-dd", $null)
        }
        catch { return }
        if ($dt -lt $cutoff) { return }
        Invoke-ArchiveForDate $leaf
    }
}
elseif ($Date.Trim()) {
    Invoke-ArchiveForDate $Date.Trim()
}
else {
    Write-Error "Pass -Date YYYY-MM-DD or -ScanOutputsDays N"
}
