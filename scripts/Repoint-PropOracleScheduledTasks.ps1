#requires -Version 5.1
<#
.SYNOPSIS
  Point PropOracle scheduled tasks at a new repo root (e.g. after moving clone to H:).

.DESCRIPTION
  Run in elevated PowerShell. Replaces OldRepoRoot in each task's action arguments and
  WorkingDirectory. Known task names come from schtasks /query; add more if you created extras.

  Prefer long-term: cd <new repo>; .\scripts\Register_Daily_Task.ps1 (recreates PropOracle-* tasks).
  Use this script for orphaned tasks (PropORACLE_*, etc.) that Register_Daily_Task.ps1 does not touch.
#>
param(
    [string]$NewRepoRoot = "H:\halek\ProfileFromC\Desktop\PropORACLE",
    [string]$OldRepoRoot = "C:\Users\halek\OneDrive\Desktop\PropORACLE",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
# Dry-run is safe without elevation; applying changes requires admin for Set-ScheduledTask.
if (-not $WhatIf) {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Run without -WhatIf in an elevated (Administrator) PowerShell window."
        exit 1
    }
}

$NewRepoRoot = $NewRepoRoot.TrimEnd('\')
$OldRepoRoot = $OldRepoRoot.TrimEnd('\')

$names = @(
    "PropOracle - Grader 5AM",
    "PropOracle - Daily 7AM",
    "PropOracle - Refresh 9AM",
    "PropOracle - Refresh 11AM",
    "PropORACLE Daily Pipeline",
    "PropORACLE_AllSports_Daily",
    "PropORACLE_NBA_LateFetch"
)

foreach ($taskName in $names) {
    $st = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $st) {
        Write-Host "[skip] not found: $taskName" -ForegroundColor DarkGray
        continue
    }
    $a = @($st.Actions)[0]
    $oldArgs = [string]$a.Arguments
    $oldWd = [string]$a.WorkingDirectory
    $newArgs = $oldArgs.Replace($OldRepoRoot, $NewRepoRoot)
    $newWd = if ($oldWd) { $oldWd.Replace($OldRepoRoot, $NewRepoRoot) } else { "" }

    if ($newArgs -eq $oldArgs -and ($newWd -eq $oldWd -or -not $oldWd)) {
        Write-Host "[ok] already current: $taskName" -ForegroundColor Green
        continue
    }

    Write-Host "[fix] $taskName" -ForegroundColor Cyan
    Write-Host "      was: $($a.Execute) $oldArgs"
    Write-Host "      wd:  $oldWd"
    Write-Host "      -> : $($a.Execute) $newArgs"

    if ($WhatIf) { continue }

    $na = if ($newWd) {
        New-ScheduledTaskAction -Execute $a.Execute -Argument $newArgs -WorkingDirectory $newWd
    } else {
        New-ScheduledTaskAction -Execute $a.Execute -Argument $newArgs
    }

    Set-ScheduledTask `
        -TaskName $st.TaskName `
        -TaskPath $st.TaskPath `
        -Action $na `
        -Trigger $st.Triggers `
        -Settings $st.Settings `
        -Principal $st.Principal | Out-Null
    Write-Host "      updated." -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Verify:" -ForegroundColor Yellow
Write-Host '  schtasks /query /fo LIST /v | findstr /i "PropOracle PropORACLE"' -ForegroundColor DarkGray
