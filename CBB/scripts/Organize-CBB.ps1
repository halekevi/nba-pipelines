# ============================================================
#  Organize-CBB.ps1
#  Reorganizes the CBB folder by type and function,
#  archives unnecessary files, and removes redundant artifacts.
#
#  Usage (from repo root):
#    .\CBB\scripts\Organize-CBB.ps1
#    .\CBB\scripts\Organize-CBB.ps1 -CBBRoot "C:\path\to\CBB"
#    .\CBB\scripts\Organize-CBB.ps1 -DryRun   (preview only, no changes made)
# ============================================================

param(
    [string]$CBBRoot = "",
    [switch]$DryRun
)
if ([string]::IsNullOrWhiteSpace($CBBRoot)) {
    # Script lives at CBB\scripts\Organize-CBB.ps1 — default folder is parent CBB.
    $CBBRoot = Split-Path -Parent $PSScriptRoot
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Log-Info  ($msg) { Write-Host "  $msg" -ForegroundColor Cyan }
function Log-Ok    ($msg) { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Log-Skip  ($msg) { Write-Host "  [--]  $msg" -ForegroundColor DarkGray }
function Log-Warn  ($msg) { Write-Host "  [!!]  $msg" -ForegroundColor Yellow }
function Log-Head  ($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Magenta }

function Safe-Move ($src, $dst) {
    if (-not (Test-Path $src)) { Log-Skip "Not found, skipping: $src"; return }
    $dstDir = Split-Path $dst -Parent
    if ($DryRun) {
        Log-Info "DRYRUN move: $src  →  $dst"
        return
    }
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
    Move-Item -Path $src -Destination $dst -Force
    Log-Ok "Moved: $(Split-Path $src -Leaf)  →  $dst"
}

function Safe-Delete ($path, $reason) {
    if (-not (Test-Path $path)) { Log-Skip "Already gone: $path"; return }
    if ($DryRun) {
        Log-Info "DRYRUN delete [$reason]: $path"
        return
    }
    Remove-Item -Path $path -Force
    Log-Ok "Deleted [$reason]: $(Split-Path $path -Leaf)"
}

function Safe-MkDir ($path) {
    if (-not (Test-Path $path)) {
        if (-not $DryRun) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
        Log-Info "Created dir: $path"
    }
}

# ── Validate root ─────────────────────────────────────────────────────────────

$CBBRoot = (Resolve-Path $CBBRoot -ErrorAction SilentlyContinue)?.Path
if (-not $CBBRoot -or -not (Test-Path $CBBRoot)) {
    Write-Host "`nERROR: CBB folder not found. Pass the correct path with -CBBRoot." -ForegroundColor Red
    Write-Host "Example: .\CBB\scripts\Organize-CBB.ps1 -CBBRoot 'C:\Users\You\Downloads\CBB'" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n============================================================" -ForegroundColor Blue
Write-Host "  CBB Pipeline Organizer" -ForegroundColor Blue
if ($DryRun) { Write-Host "  MODE: DRY RUN — no files will be changed" -ForegroundColor Yellow }
Write-Host "  Root: $CBBRoot" -ForegroundColor Blue
Write-Host "============================================================`n" -ForegroundColor Blue

# ── Step 1: Create new folder structure ──────────────────────────────────────

Log-Head "1. Creating folder structure"

$dirs = @(
    "scripts\pipeline",
    "scripts\grading",
    "scripts\utilities",
    "data\reference",
    "data\cache",
    "outputs\2026-02-22",
    "ui",
    "docs",
    "archive\old_outputs",
    "archive\old_slates",
    "archive\old_versions"
)

foreach ($d in $dirs) {
    Safe-MkDir (Join-Path $CBBRoot $d)
}

# ── Step 2: Move pipeline scripts ─────────────────────────────────────────────

Log-Head "2. Organizing pipeline scripts  →  scripts\pipeline\"

Safe-Move `
    (Join-Path $CBBRoot "pp_cbb_scraper.py") `
    (Join-Path $CBBRoot "scripts\pipeline\step1_pp_cbb_scraper.py")

Safe-Move `
    (Join-Path $CBBRoot "cbb_step2_normalize.py") `
    (Join-Path $CBBRoot "scripts\pipeline\step2_normalize.py")

Safe-Move `
    (Join-Path $CBBRoot "cbb_step3b_attach_def_rankings.py") `
    (Join-Path $CBBRoot "scripts\pipeline\step3b_attach_def_rankings.py")

Safe-Move `
    (Join-Path $CBBRoot "step5_attach_espn_ids.py") `
    (Join-Path $CBBRoot "scripts\pipeline\step5a_attach_espn_ids.py")

Safe-Move `
    (Join-Path $CBBRoot "cbb_step5b_attach_boxscore_stats.py") `
    (Join-Path $CBBRoot "scripts\pipeline\step5b_attach_boxscore_stats.py")

Safe-Move `
    (Join-Path $CBBRoot "cbb_step6_rank_props.py") `
    (Join-Path $CBBRoot "scripts\pipeline\step6_rank_props.py")

# ── Step 3: Move grading scripts ──────────────────────────────────────────────

Log-Head "3. Organizing grading scripts  →  scripts\grading\"

Safe-Move `
    (Join-Path $CBBRoot "fetch_cbb_actuals_by_date.py") `
    (Join-Path $CBBRoot "scripts\grading\fetch_actuals_by_date.py")

Safe-Move `
    (Join-Path $CBBRoot "cbb_grader.py") `
    (Join-Path $CBBRoot "scripts\grading\cbb_grader.py")

Safe-Move `
    (Join-Path $CBBRoot "grade_cbb_full_slate.py") `
    (Join-Path $CBBRoot "scripts\grading\grade_full_slate.py")

# ── Step 4: Move utility scripts ──────────────────────────────────────────────

Log-Head "4. Organizing utility scripts  →  scripts\utilities\"

Safe-Move `
    (Join-Path $CBBRoot "build_ncaa_mbb_espn_athletes_master.py") `
    (Join-Path $CBBRoot "scripts\utilities\build_espn_athletes_master.py")

# ── Step 5: Move reference data ───────────────────────────────────────────────

Log-Head "5. Organizing reference data  →  data\reference\"

# Move from data/ subfolder if it still has its own copies
foreach ($f in @("cbb_def_rankings.csv", "grader_template.csv", "grader_template_cbb.csv", "ncaa_mbb_athletes_master.csv")) {
    $src = Join-Path $CBBRoot "data\$f"
    $dst = Join-Path $CBBRoot "data\reference\$f"
    if (Test-Path $src) { Safe-Move $src $dst }
}

# Rename schema file and move
Safe-Move `
    (Join-Path $CBBRoot "step0_pp_schema.txt") `
    (Join-Path $CBBRoot "data\reference\pp_api_schema.txt")

# Root-level duplicates → delete (canonical copy is in data\reference\)
Safe-Delete `
    (Join-Path $CBBRoot "cbb_def_rankings.csv") `
    "duplicate of data\reference\ version"

Safe-Delete `
    (Join-Path $CBBRoot "ncaa_mbb_athletes_master.csv") `
    "duplicate of data\reference\ version"

# ── Step 6: Move cache ────────────────────────────────────────────────────────

Log-Head "6. Moving boxscore cache  →  data\cache\"

Safe-Move `
    (Join-Path $CBBRoot "cbb_boxscore_cache.csv") `
    (Join-Path $CBBRoot "data\cache\cbb_boxscore_cache.csv")

# ── Step 7: Move UI ───────────────────────────────────────────────────────────

Log-Head "7. Moving UI component  →  ui\"

Safe-Move `
    (Join-Path $CBBRoot "pipeline_dashboard.jsx") `
    (Join-Path $CBBRoot "ui\pipeline_dashboard.jsx")

# ── Step 8: Organize dated outputs ────────────────────────────────────────────

Log-Head "8. Organizing dated outputs  →  outputs\YYYY-MM-DD\"

# 2026-02-22 outputs (from root)
foreach ($f in @(
    "cbb_actuals_2026-02-22.csv",
    "cbb_graded_2026-02-22.xlsx",
    "cbb_tickets.xlsx"
)) {
    $src = Join-Path $CBBRoot $f
    if (Test-Path $src) {
        Safe-Move $src (Join-Path $CBBRoot "outputs\2026-02-22\$f")
    }
}

# 2026-02-24 actuals
$src24 = Join-Path $CBBRoot "cbb_actuals_2026-02-24.csv"
if (Test-Path $src24) {
    Safe-MkDir (Join-Path $CBBRoot "outputs\2026-02-24")
    Safe-Move $src24 (Join-Path $CBBRoot "outputs\2026-02-24\cbb_actuals_2026-02-24.csv")
}

# Move existing outputs\2026-02-22 contents if they weren't already moved
$oldOut = Join-Path $CBBRoot "outputs\2026-02-22"
if (Test-Path $oldOut) {
    # Keep only key finals; intermediate steps from that date can stay as-is
    Log-Skip "outputs\2026-02-22 already exists — contents preserved as-is"
}

# ── Step 9: Archive old versions ──────────────────────────────────────────────

Log-Head "9. Archiving old script versions  →  archive\old_versions\"

foreach ($f in @(
    "attach_cbb_athlete_ids_FIXED.py",
    "fetch_cbb_actuals_by_date_V2.py"
)) {
    $src = Join-Path $CBBRoot "archive\old_versions\$f"
    # Already in old_versions from original zip — just confirm
    if (Test-Path $src) {
        Log-Skip "Already archived: $f"
    }
}

# ── Step 10: Delete intermediate root-level CSVs ──────────────────────────────

Log-Head "10. Deleting redundant root-level intermediate CSVs"

$intermediates = @(
    "step1_cbb.csv",
    "step1_fetch_prizepicks_api_cbb.csv",
    "step2_cbb.csv",
    "step2_normalized_cbb.csv",
    "step3_cbb.csv",
    "step3b_with_def_rankings_cbb.csv",
    "step3b_with_defense_cbb.csv",
    "step5_with_espn_ids_cbb.csv",
    "step5b_cbb.csv",
    "step5b_with_stats_cbb.csv",
    "step6_ranked_props_cbb.csv"
)

foreach ($f in $intermediates) {
    Safe-Delete (Join-Path $CBBRoot $f) "intermediate run artifact — recreated each run"
}

# Root-level ranked xlsx (outputs copy is the keeper)
Safe-Delete (Join-Path $CBBRoot "step6_ranked_cbb.xlsx")       "intermediate — kept in outputs\2026-02-22\"
Safe-Delete (Join-Path $CBBRoot "step6_ranked_props_cbb.xlsx")  "intermediate — kept in outputs\2026-02-22\"

# ── Step 11: Clean up empty legacy data/ subfolder ────────────────────────────

Log-Head "11. Cleaning up legacy data\ subfolder"

$legacyData = Join-Path $CBBRoot "data"
if (Test-Path $legacyData) {
    $remaining = Get-ChildItem $legacyData -File -Recurse
    if ($remaining.Count -eq 0) {
        Safe-Delete $legacyData "empty after migration to data\reference\"
    } else {
        Log-Skip "data\ still has files — not removed: $($remaining.Name -join ', ')"
    }
}

# ── Done: print final tree ────────────────────────────────────────────────────

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  Reorganization complete!" -ForegroundColor Green
if ($DryRun) { Write-Host "  (DRY RUN — no actual changes were made)" -ForegroundColor Yellow }
Write-Host "============================================================" -ForegroundColor Green

Write-Host "`nFinal structure:" -ForegroundColor Cyan
if (-not $DryRun -and (Test-Path $CBBRoot)) {
    Get-ChildItem $CBBRoot -Recurse |
        Where-Object { -not $_.PSIsContainer } |
        ForEach-Object {
            $rel = $_.FullName.Substring($CBBRoot.Length + 1)
            Write-Host "  $rel"
        }
}

Write-Host "`nNext steps:" -ForegroundColor Yellow
Write-Host "  1. Update any import paths in scripts if they reference sibling files"
Write-Host "  2. Run pipeline from scripts\pipeline\ directory"
Write-Host "  3. Update cbb_def_rankings.csv weekly in data\reference\"
Write-Host "  4. Boxscore cache lives in data\cache\ — don't delete it"
Write-Host ""
