# ============================================================
#  Register_Daily_Task.ps1
#  PropOracle – Master Pipeline  (all sports, daily 6:00 AM local)
#
#  Run ONCE from an elevated (Administrator) PowerShell prompt:
#    Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#    .\Register_Daily_Task.ps1
# ============================================================

# ─── CONFIGURATION ───────────────────────────────────────────

# Root folder that contains run_pipeline.ps1
$PipelineRoot   = "C:\Users\halek\OneDrive\Desktop\Vision Board\NbaPropPipelines\PropOracle"

# The master runner
$MasterScript   = Join-Path $PipelineRoot "run_pipeline.ps1"

# Sports flags to pass on every daily run.
# Default: NBA + CBB + NHL + Soccer (the active daily sports).
# Add -IncludeMLB when the MLB season starts.
$DailyFlags     = "-IncludeNHL -IncludeSoccer"

# Run time (daily recurrence; Windows Task Scheduler uses local time)
$RunHour        = 6
$RunMinute      = 0

# Task identity
$TaskName       = "PropOracle - Master Pipeline Daily"
$TaskDesc       = "PropOracle daily prop pipeline: NBA + CBB + NHL + Soccer + Combined at 6:00 AM"
# ─────────────────────────────────────────────────────────────

if (-not (Test-Path $MasterScript)) {
    Write-Error "Master script not found: $MasterScript`nUpdate `$PipelineRoot in this file."
    exit 1
}

$PowerShellExe = (Get-Command powershell.exe).Source

# Full argument string passed to powershell.exe
$Argument = "-NonInteractive -ExecutionPolicy Bypass -File `"$MasterScript`" $DailyFlags"

$Action  = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Argument

$Trigger = New-ScheduledTaskTrigger -Daily -At "${RunHour}:$('{0:D2}' -f $RunMinute)"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit    (New-TimeSpan -Hours 3)  `  # full multi-sport run can take a while
    -RestartCount          2                         `
    -RestartInterval       (New-TimeSpan -Minutes 15) `
    -StartWhenAvailable                              `  # run ASAP if machine was off at scheduled time
    -RunOnlyIfNetworkAvailable                       `
    -MultipleInstances     IgnoreNew

# Idempotent re-registration
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Task = Register-ScheduledTask `
    -TaskName    $TaskName `
    -Description $TaskDesc `
    -Action      $Action `
    -Trigger     $Trigger `
    -Settings    $Settings `
    -RunLevel    Limited `
    -Force

if ($Task) {
    Write-Host ""
    Write-Host "✅  Task registered!" -ForegroundColor Green
    Write-Host "    Name      : $TaskName"
    Write-Host "    Script    : $MasterScript"
    Write-Host "    Flags     : $DailyFlags"
    Write-Host "    Runs at   : $RunHour:$('{0:D2}' -f $RunMinute) daily"
    Write-Host "    Catch-up  : Yes (StartWhenAvailable)"
    Write-Host ""
    Write-Host "Quick commands:"
    Write-Host "  # Test run right now:"
    Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
    Write-Host ""
    Write-Host "  # Check last run status:"
    Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName' | Select LastRunTime, LastTaskResult"
    Write-Host ""
    Write-Host "  # Change sports flags (e.g. add MLB for baseball season):"
    Write-Host "  # Edit `$DailyFlags in this script and re-run it."
    Write-Host ""
    Write-Host "  # Remove task:"
    Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
} else {
    Write-Error "Task registration failed."
    exit 1
}

