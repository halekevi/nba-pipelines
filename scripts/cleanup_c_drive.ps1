# Safe C: drive cleanup — keeps Android SDK system-images, OneDrive, MongoDB, active Cursor project.
# Target: free space when C: drops below 20 GB (informational) or when invoked manually / from run_daily warning.
param(
    [int]$TargetFreeGb = 20,
    [switch]$WhatIf
)

$ErrorActionPreference = "Continue"
$UserHome = $env:USERPROFILE
$ActiveCursorProject = "h-halek-ProfileFromC-Desktop-PropORACLE"
$freedMb = 0.0

function Add-Freed([double]$Mb) {
    $script:freedMb += $Mb
}

function Remove-TreeSafe([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $before = (Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        if ($WhatIf) {
            Write-Host "[WhatIf] Would remove $Label ($([math]::Round($before/1MB,1)) MB): $Path" -ForegroundColor Yellow
            return
        }
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        Add-Freed ($before / 1MB)
        Write-Host "Removed $Label (~$([math]::Round($before/1MB,1)) MB): $Path" -ForegroundColor Green
    } catch {
        Write-Warning "Skip $Label ($Path): $($_.Exception.Message)"
    }
}

function Clear-DirContents([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-TreeSafe $_.FullName "$Label\$($_.Name)"
    }
}

$cFree = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 1)
Write-Host "C: free before cleanup: ${cFree} GB" -ForegroundColor Cyan

# User temp
Clear-DirContents (Join-Path $UserHome "AppData\Local\Temp") "User Temp"

# pip / npm caches (regenerate on use)
if (-not $WhatIf) {
    try {
        & py -3.14 -m pip cache purge 2>&1 | Out-Null
        Write-Host "pip cache purged" -ForegroundColor Green
    } catch { Write-Warning "pip cache purge failed" }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        try {
            & npm cache clean --force 2>&1 | Out-Null
            Write-Host "npm cache cleaned" -ForegroundColor Green
        } catch { Write-Warning "npm cache clean failed" }
    }
}

# Gradle caches (keep wrapper dists)
$gradleCaches = Join-Path $UserHome ".gradle\caches"
if (Test-Path $gradleCaches) {
    Get-ChildItem $gradleCaches -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "wrapper" } |
        ForEach-Object { Remove-TreeSafe $_.FullName "Gradle cache $($_.Name)" }
}

# Stale Cursor project DBs (path no longer exists on disk)
$cursorProj = Join-Path $env:LOCALAPPDATA "Cursor\User\workspaceStorage"
if (Test-Path $cursorProj) {
    Get-ChildItem $cursorProj -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $wsJson = Join-Path $_.FullName "workspace.json"
        if (-not (Test-Path $wsJson)) { return }
        try {
            $raw = Get-Content $wsJson -Raw -ErrorAction Stop | ConvertFrom-Json
            $folder = [string]$raw.folder
            if ($folder -and -not (Test-Path -LiteralPath $folder)) {
                if ($folder -match [regex]::Escape($ActiveCursorProject)) { return }
                Remove-TreeSafe $_.FullName "Stale Cursor workspace $($_.Name)"
            }
        } catch { }
    }
}

# Browser caches (optional, safe)
foreach ($browserCache in @(
    (Join-Path $UserHome "AppData\Local\Google\Chrome\User Data\Default\Cache"),
    (Join-Path $UserHome "AppData\Local\Microsoft\Edge\User Data\Default\Cache")
)) {
    Clear-DirContents $browserCache "Browser cache"
}

# Windows crash dumps (user-visible)
$crashDir = Join-Path $UserHome "AppData\Local\CrashDumps"
Clear-DirContents $crashDir "Crash dumps"

$cFreeAfter = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 1)
Write-Host ""
Write-Host "Estimated freed: ~$([math]::Round($freedMb,1)) MB" -ForegroundColor Cyan
Write-Host "C: free after cleanup: ${cFreeAfter} GB (target ${TargetFreeGb} GB)" -ForegroundColor Cyan
if ($cFreeAfter -lt $TargetFreeGb) {
    Write-Warning "Still below ${TargetFreeGb} GB free — consider manual review (do not delete Android SDK system-images or OneDrive)."
}
