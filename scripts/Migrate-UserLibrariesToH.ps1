<#
.SYNOPSIS
  Move large Windows user-library folders from C: to H:, then replace each
  original path with a directory junction so apps keep working.

.DESCRIPTION
  By default migrates: Videos, Music, Pictures, Downloads, Documents, Desktop.
  OneDrive is NOT migrated (would break sync); use -IncludeOneDrive at your own risk.

  Run in Windows PowerShell 5.1 or PowerShell 7+ **outside** Cursor if your
  agent/sandbox cannot write to H:\.

  Requires: write access to H:\; robocopy.exe; cmd.exe for mklink /J.

.PARAMETER DestinationRoot
  Folder on H: where library contents are stored (created if missing).

.PARAMETER WhatIf
  Show what would run without copying or creating junctions.

.EXAMPLE
  .\Migrate-UserLibrariesToH.ps1
.EXAMPLE
  .\Migrate-UserLibrariesToH.ps1 -DestinationRoot 'H:\LibraryData_20260411' -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [Parameter(Mandatory = $false)]
  [string] $DestinationRoot = ("H:\from_C_{0}" -f (Get-Date -Format 'yyyyMMdd_HHmm')),

  [Parameter(Mandatory = $false)]
  [string[]] $Folders = @('Videos', 'Music', 'Pictures', 'Downloads', 'Documents', 'Desktop'),

  [switch] $IncludeOneDrive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LibrarySourcePath {
  param([Parameter(Mandatory = $true)][string] $Name)
  switch ($Name) {
    'Desktop' { return [Environment]::GetFolderPath('Desktop') }
    'Documents' { return [Environment]::GetFolderPath('MyDocuments') }
    'Music' { return [Environment]::GetFolderPath('MyMusic') }
    'Pictures' { return [Environment]::GetFolderPath('MyPictures') }
    'Videos' { return [Environment]::GetFolderPath('MyVideos') }
    'Downloads' { return (Join-Path $env:USERPROFILE 'Downloads') }
    'OneDrive' { return (Join-Path $env:USERPROFILE 'OneDrive') }
    default { throw "Unknown library name: $Name" }
  }
}

function Test-RobocopySuccess {
  param([int] $ExitCode)
  # Robocopy: 0–7 = success with various "nothing to copy" etc.; ≥8 = failure
  return ($ExitCode -lt 8)
}

if (-not (Test-Path -LiteralPath 'H:\')) {
  throw "H:\ is not available. Connect the drive and retry."
}

if (-not $WhatIfPreference) {
  $testFile = Join-Path 'H:\' ('_write_test_{0}.tmp' -f [guid]::NewGuid().ToString('N'))
  try {
    [System.IO.File]::WriteAllText($testFile, 'ok')
    Remove-Item -LiteralPath $testFile -Force
  }
  catch {
    throw "Cannot write to H:\ ($($_.Exception.Message)). Run this script in a normal (non-sandbox) PowerShell window."
  }
}

if (-not $IncludeOneDrive -and ($Folders -contains 'OneDrive')) {
  throw "Refusing to migrate OneDrive unless you pass -IncludeOneDrive (sync will break if misused)."
}

New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
$logDir = Join-Path $DestinationRoot '_logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Host "Destination: $DestinationRoot"
Write-Host "Log directory: $logDir"

foreach ($folder in $Folders) {
  if ($folder -eq 'OneDrive' -and -not $IncludeOneDrive) { continue }

  $src = Get-LibrarySourcePath -Name $folder
  $dst = Join-Path $DestinationRoot $folder

  Write-Host ""
  Write-Host "=== $folder ===" -ForegroundColor Cyan
  Write-Host "Source: $src"
  Write-Host "Target: $dst"

  if (-not (Test-Path -LiteralPath $src)) {
    Write-Warning "Source missing; skipping: $src"
    continue
  }

  # Skip if source is already a junction/reparse point (avoid double-migration)
  $srcItem = Get-Item -LiteralPath $src -Force
  if ($srcItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    Write-Warning "Source is already a reparse point; skipping: $src"
    continue
  }

  if ($PSCmdlet.ShouldProcess($src, "robocopy /MOVE to $dst then junction")) {
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    $logFile = Join-Path $logDir ("robocopy_{0}.log" -f $folder)
    $roboArgs = @(
      $src, $dst,
      '/MOVE', '/E',
      '/COPY:DAT',
      '/R:2', '/W:5',
      '/MT:8',
      '/XJ',
      '/XD', 'node_modules',
      ('/LOG+:{0}' -f $logFile),
      '/TEE'
    )
    & robocopy.exe @roboArgs
    $rc = $LASTEXITCODE
    if (-not (Test-RobocopySuccess -ExitCode $rc)) {
      throw "robocopy failed for $folder (exit $rc). See log: $logFile"
    }

    # Remove leftover empty tree on C: (robocopy /MOVE can leave empty dirs)
    if (Test-Path -LiteralPath $src) {
      Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $src) {
      throw "Could not remove original folder (files may be in use): $src`nClose apps using this folder and rerun for this library only."
    }

    $mk = "mklink /J `"$src`" `"$dst`""
    Write-Host "Creating junction: $mk"
    cmd.exe /c $mk | Write-Host
    if ($LASTEXITCODE -ne 0) {
      throw "mklink failed for $folder (exit $LASTEXITCODE). Data is on H: at $dst — restore manually by moving back or creating the junction yourself."
    }
  }
}

Write-Host ""
Write-Host "Done. Bulk data should be under $DestinationRoot with junctions from your profile paths." -ForegroundColor Green
