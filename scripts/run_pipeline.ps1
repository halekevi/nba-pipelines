#requires -Version 7.2
<#
.SYNOPSIS
  Forwarder to the canonical multi-sport pipeline at repo root.

.DESCRIPTION
  Prefer running:  pwsh .\run_pipeline.ps1  from the PropORACLE root.
  This script exists so paths like  pwsh .\scripts\run_pipeline.ps1  stay valid
  without maintaining a second copy of the full pipeline (avoids drift).
#>
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Canonical = Join-Path $RepoRoot "run_pipeline.ps1"
if (-not (Test-Path -LiteralPath $Canonical)) {
    Write-Error "Canonical pipeline not found: $Canonical"
    exit 1
}
# No param() here — forward all CLI args to the root script unchanged.
$psExe = (Get-Process -Id $PID).Path
& $psExe -NoProfile -File $Canonical @args
exit $LASTEXITCODE
