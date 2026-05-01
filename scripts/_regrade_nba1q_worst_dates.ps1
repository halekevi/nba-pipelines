# One-off: re-grade worst NBA1Q void dates (delete after use or keep local).
$dates = @(
    "2026-03-26",
    "2026-03-24",
    "2026-03-30",
    "2026-03-31",
    "2026-04-01",
    "2026-04-02",
    "2026-04-03",
    "2026-04-04"
)
$grader = Join-Path $PSScriptRoot "run_grader.ps1"
foreach ($d in $dates) {
    Write-Host "==== GRADE $d ====" -ForegroundColor Cyan
    & $grader -Date $d
}
