# Opens Android Studio for the Capacitor project in this directory (run from repo clone).
$ErrorActionPreference = "Stop"
$MobileDir = $PSScriptRoot
$AndroidDir = Join-Path $MobileDir "android"
if (-not (Test-Path $AndroidDir)) {
    Write-Error "Missing Android project: $AndroidDir"
}
Set-Location $MobileDir
npx cap open android
