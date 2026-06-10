<#
.SYNOPSIS
  Re-grade the last N calendar days and rebuild the leg ML retrain dataset.

.DESCRIPTION
  Thin wrapper around regrade_retrain_window.ps1 for weekly feedback-loop hygiene
  after grader fixes, void reconciliation, or actuals backfills.

.EXAMPLE
  .\scripts\regrade_recent_window.ps1
  .\scripts\regrade_recent_window.ps1 -Days 14 -RunRebuild
#>
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [int]$Days = 7,
    [switch]$RunRebuild,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $RepoRoot).Path
$From = (Get-Date).AddDays(-1 * [Math]::Max(1, $Days)).ToString("yyyy-MM-dd")
$Wrapper = Join-Path $Root "scripts\regrade_retrain_window.ps1"

if (-not (Test-Path $Wrapper)) {
    throw "Missing $Wrapper"
}

$argsList = @("-RepoRoot", $Root, "-From", $From)
if ($RunRebuild) { $argsList += "-RunRebuild" }
if ($DryRun) { $argsList += "-DryRun" }

Write-Host "[regrade-recent] From=$From Days=$Days RunRebuild=$RunRebuild DryRun=$DryRun" -ForegroundColor Cyan
& $Wrapper @argsList
