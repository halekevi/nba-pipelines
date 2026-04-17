# ============================================================
#  Register_Daily_Task.ps1
#  PropOracle automation scheduler:
#   - 5:00 AM  grader
#   - 7:00 AM  initial daily pipeline
#   - 9:00 AM  refresh + add/remove diff log
#   - 11:00 AM refresh + add/remove diff log
#
# Run elevated from the repo you want tasks to use (e.g. H:\...\PropORACLE\scripts).
# Re-running replaces tasks so paths stay in sync after moving the clone off OneDrive.
# ============================================================

$PipelineRoot = Split-Path -Parent $PSScriptRoot
$PowerShellExe = (Get-Command powershell.exe).Source

$Script5 = Join-Path $PipelineRoot "scripts\run_grader_5am.ps1"
$Script7 = Join-Path $PipelineRoot "scripts\run_daily_7am.ps1"
$ScriptRefresh = Join-Path $PipelineRoot "scripts\run_refresh_with_log.ps1"

foreach ($s in @($Script5, $Script7, $ScriptRefresh)) {
    if (-not (Test-Path $s)) {
        Write-Error "Required script missing: $s"
        exit 1
    }
}

function Register-PropTask {
    param(
        [string]$TaskName,
        [string]$Description,
        [string]$ScriptPath,
        [string]$At,
        [string]$ExtraArgs = ""
    )

    $arg = "-NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`" $ExtraArgs"
    $action  = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arg
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
        -RestartCount 2 `
        -RestartInterval (New-TimeSpan -Minutes 15) `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable `
        -MultipleInstances IgnoreNew

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description $Description `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -RunLevel Limited `
        -Force | Out-Null
}

Register-PropTask `
    -TaskName "PropOracle - Grader 5AM" `
    -Description "Pull latest, run grader for yesterday." `
    -ScriptPath $Script5 `
    -At "05:00"

Register-PropTask `
    -TaskName "PropOracle - Daily 7AM" `
    -Description "Pull latest, run initial daily pipeline, and log fetched props snapshot." `
    -ScriptPath $Script7 `
    -At "07:00"

Register-PropTask `
    -TaskName "PropOracle - Refresh 9AM" `
    -Description "Refresh props, update outputs, and log added/removed props." `
    -ScriptPath $ScriptRefresh `
    -At "09:00" `
    -ExtraArgs "-RunLabel 9AM"

Register-PropTask `
    -TaskName "PropOracle - Refresh 11AM" `
    -Description "Refresh props, update outputs, and log added/removed props." `
    -ScriptPath $ScriptRefresh `
    -At "11:00" `
    -ExtraArgs "-RunLabel 11AM"

Write-Host ""
Write-Host "✅ Scheduler tasks registered." -ForegroundColor Green
Write-Host "  - PropOracle - Grader 5AM"
Write-Host "  - PropOracle - Daily 7AM"
Write-Host "  - PropOracle - Refresh 9AM"
Write-Host "  - PropOracle - Refresh 11AM"
Write-Host ""
Write-Host "Quick checks:"
Write-Host "  Get-ScheduledTask | Where-Object TaskName -like 'PropOracle -*' | Select-Object TaskName, State"
Write-Host "  Get-ScheduledTaskInfo -TaskName 'PropOracle - Daily 7AM' | Select LastRunTime, LastTaskResult"

