# Opens Android Studio on the canonical native project under this machine's H: clone.
# Capacitor resolves android/ relative to this folder; do not run from a OneDrive duplicate.
$ErrorActionPreference = "Stop"
$MobileDir = "H:\halek\ProfileFromC\Desktop\PropORACLE\mobile"
$AndroidDir = Join-Path $MobileDir "android"
if (-not (Test-Path $AndroidDir)) {
    Write-Error "Missing Android project: $AndroidDir"
}
Set-Location $MobileDir
npx cap open android
