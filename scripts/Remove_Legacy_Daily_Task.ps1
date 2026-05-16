#requires -Version 5.1
<#
.SYNOPSIS
  Remove legacy "PropORACLE Daily Pipeline" — duplicates "PropOracle - Daily 7AM" at 7:00.

.DESCRIPTION
  At 7 AM you may see two windows: [7AM DAILY] (run_daily_7am.ps1) and a bare
  run_daily.ps1 log. The second comes from this old task. Run elevated once.

  Keep: PropOracle - Daily 7AM  →  scripts\run_daily_7am.ps1  (git pull + run_daily)

.EXAMPLE
  # Right-click PowerShell → Run as administrator, then:
  cd H:\halek\ProfileFromC\Desktop\PropORACLE\scripts
  .\Remove_Legacy_Daily_Task.ps1
#>
$ErrorActionPreference = "Stop"
$Legacy = "PropORACLE Daily Pipeline"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Re-run this script in an elevated (Administrator) PowerShell window." -ForegroundColor Red
    exit 1
}

$st = Get-ScheduledTask -TaskName $Legacy -ErrorAction SilentlyContinue
if (-not $st) {
    Write-Host "Already removed: $Legacy" -ForegroundColor Green
    exit 0
}

try {
    Stop-ScheduledTask -TaskName $Legacy -ErrorAction SilentlyContinue
} catch { }

Unregister-ScheduledTask -TaskName $Legacy -Confirm:$false
Write-Host "Removed: $Legacy" -ForegroundColor Green
Write-Host "7 AM will use only: PropOracle - Daily 7AM" -ForegroundColor Cyan
