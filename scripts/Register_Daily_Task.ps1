# ============================================================
#  Register_Daily_Task.ps1
#  PropOracle – Master Pipeline  (all sports, daily 8 AM)
#
#  Run ONCE from an elevated (Administrator) PowerShell prompt:
#    Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#    .\Register_Daily_Task.ps1
# ============================================================

# ─── CONFIGURATION ───────────────────────────────────────────

# Repo root (folder that contains run_pipeline.ps1). Default: parent of scripts\.
$PipelineRoot   = Split-Path -Parent $PSScriptRoot

# Pulls origin (fast-forward), then run_pipeline.ps1 -SkipFetch (inputs from repo + local disk).
$MasterScript   = Join-Path $PipelineRoot "scripts\run_daily_from_git.ps1"

# Extra args for run_daily_from_git.ps1 (e.g. -SkipDailyGrader). -SkipFetch is always applied inside the wrapper.
$DailyFlags     = ""

# Run time
$RunHour        = 8
$RunMinute      = 0

# Task identity
$TaskName       = "PropOracle - Master Pipeline Daily"
$TaskDesc       = "PropOracle: git pull --ff-only, then pipeline -SkipFetch + combined + grades"
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
    -StartWhenAvailable                              `  # run ASAP if machine was off at 8 AM
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
    Write-Host "  # Edit `$DailyFlags e.g. -SkipDailyGrader (see scripts\run_daily_from_git.ps1)."
    Write-Host ""
    Write-Host "  # Remove task:"
    Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
} else {
    Write-Error "Task registration failed."
    exit 1
}

