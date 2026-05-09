# Dot-sourced by run_pipeline.ps1 (scripts\ or repo root).
# Requires in caller scope: $Root, $Date, $SkipDailyGrader, and Run-GitPushGradeArtifacts.

function Run-PostPipelineGrader {
    if ($SkipDailyGrader) {
        Write-Host "`n[ GRADES ] SkipDailyGrader — not running post-pipeline grader" -ForegroundColor DarkGray
        return
    }

    $outRoot = Join-Path $Root "outputs"
    if (Test-Path $outRoot) {
        $graded = @(
            Get-ChildItem -Path $outRoot -Recurse -Filter "combined_tickets_graded_*.xlsx" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        ) | Select-Object -First 1
        if ($graded) {
            Write-Host "[PostGrader] Found: $($graded.Name)" -ForegroundColor DarkGray
        } else {
            Write-Host "[PostGrader] No graded file found — skipping" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "[PostGrader] No graded file found — skipping" -ForegroundColor DarkGray
    }

    try {
        $dt = [datetime]::ParseExact($Date, "yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {
        Write-Host "`n[ GRADES ] Could not parse pipeline date '$Date' — skip grader" -ForegroundColor Yellow
        return
    }
    $gradeDate = $dt.AddDays(-1).ToString("yyyy-MM-dd")
    Write-Host ""
    Write-Host "[ GRADES ] Post-pipeline grader (slate date $gradeDate)" -ForegroundColor Magenta

    $runner = Join-Path $Root "scripts\run_grader.ps1"
    if (-not (Test-Path $runner)) {
        Write-Host "  scripts\run_grader.ps1 not found — skip" -ForegroundColor Yellow
        return
    }
    try {
        & $runner -Date $gradeDate
    } catch {
        Write-Host "  [ GRADES ] run_grader.ps1 failed: $_" -ForegroundColor Yellow
    }
    try {
        Run-GitPushGradeArtifacts -GradeDate $gradeDate
    } catch {
        Write-Host "  [ GRADES ] Run-GitPushGradeArtifacts failed: $_" -ForegroundColor Yellow
    }

    # Income (/income): keep bundled template in sync for deploy hosts without a volume.
    $syncGh = Join-Path $Root "scripts\sync_grade_history_to_templates.py"
    if (Test-Path $syncGh) {
        try {
            Push-Location $Root
            & py -3.14 $syncGh
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [ GRADES ] sync_grade_history_to_templates exit $LASTEXITCODE (optional)" -ForegroundColor DarkGray
            }
        } catch {
            Write-Host "  [ GRADES ] sync_grade_history_to_templates: $_" -ForegroundColor DarkGray
        } finally {
            Pop-Location
        }
    }
}
