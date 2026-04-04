# PropOracle index.html + app.py patcher
# Run from repo root: .\scripts\patch_slateiq.ps1

$Root      = Split-Path -Parent $PSScriptRoot
$indexPath = Join-Path $Root "ui_runner\templates\index.html"
$appPath   = Join-Path $Root "ui_runner\app.py"
$repTxt    = Join-Path $Root "archive\root-text\patch_replacement.txt"

if (-not (Test-Path -LiteralPath $repTxt)) {
    Write-Error "Missing $repTxt — restore patch_replacement.txt under archive\root-text\ or update path."
    exit 1
}

# ── 1. Patch index.html ───────────────────────────────────────────────────────
Write-Host "Patching index.html..." -ForegroundColor Cyan
$src = [System.IO.File]::ReadAllText($indexPath, [System.Text.Encoding]::UTF8)

if ($src -match 'Cason Wallace') {
    Write-Host "  Found hardcoded data - replacing..." -ForegroundColor Yellow

    $newBlock = [System.IO.File]::ReadAllText($repTxt, [System.Text.Encoding]::UTF8)

    # Find anchor positions using IndexOf (handles unicode box chars safely)
    $startAnchor = 'const PLAYER_DATA = {'
    $endAnchor   = 'function applyStatusCard'
    $s1 = $src.IndexOf($startAnchor)
    $s2 = $src.IndexOf($endAnchor)

    if ($s1 -lt 0 -or $s2 -lt 0) {
        Write-Error "Anchors not found in index.html (s1=$s1 s2=$s2)"; exit 1
    }

    $src = $src.Substring(0, $s1) + $newBlock + $src.Substring($s2)

    # Replace bare render calls at bottom
    $src = $src.Replace("renderEdges();`nrenderStreaks();", "// Data loaded dynamically`nloadSlateData();")

    [System.IO.File]::WriteAllText($indexPath, $src, [System.Text.Encoding]::UTF8)
    Write-Host "  index.html patched OK" -ForegroundColor Green

    # Verify
    $check = [System.IO.File]::ReadAllText($indexPath, [System.Text.Encoding]::UTF8)
    if ($check -match 'Cason Wallace') {
        Write-Error "FAILED - hardcoded data still present"; exit 1
    }
    if ($check -match 'loadSlateData') {
        Write-Host "  Verified: loadSlateData() present" -ForegroundColor Green
    }
} else {
    Write-Host "  index.html already patched - skipping" -ForegroundColor DarkGray
}

# ── 2. Patch app.py ───────────────────────────────────────────────────────────
Write-Host "Patching app.py..." -ForegroundColor Cyan
$app = [System.IO.File]::ReadAllText($appPath, [System.Text.Encoding]::UTF8)

if ($app -match '/api/slate') {
    Write-Host "  app.py already has /api/slate - skipping" -ForegroundColor DarkGray
} else {
    $slateEndpoint = @'

# API: Slate picks - deduped unique picks from tickets_latest.json
@app.get("/api/slate")
def api_slate():
    import json as _json
    json_path = TEMPLATES_DIR / "tickets_latest.json"
    if not json_path.exists():
        return jsonify({"picks": [], "generated_at": None, "date": None})
    try:
        data = _json.loads(json_path.read_text(encoding="utf-8-sig"))
        seen = set()
        picks = []
        for group in (data.get("groups") or []):
            for ticket in (group.get("tickets") or []):
                for leg in (ticket.get("legs") or []):
                    key = (leg.get("player"), leg.get("prop_type"), leg.get("direction"), leg.get("line"))
                    if key in seen:
                        continue
                    seen.add(key)
                    picks.append({
                        "sport":      leg.get("sport", ""),
                        "initials":   leg.get("initials", ""),
                        "player":     leg.get("player", ""),
                        "prop":       leg.get("prop_type", ""),
                        "line":       leg.get("line", 0),
                        "pick":       leg.get("pick_type", "Standard"),
                        "dir":        leg.get("direction", "OVER"),
                        "hit":        round((leg.get("hit_rate") or 0) * 100),
                        "edge":       leg.get("edge") or 0,
                        "l5_over":    leg.get("l5_over"),
                        "l5_under":   leg.get("l5_under"),
                        "l10_over":   leg.get("l10_over"),
                        "l10_under":  leg.get("l10_under"),
                        "l5_avg":     leg.get("l5_avg"),
                        "season_avg": leg.get("season_avg"),
                    })
        picks.sort(key=lambda p: abs(p["edge"]), reverse=True)
        return jsonify({"picks": picks, "generated_at": data.get("generated_at"), "date": data.get("date")})
    except Exception as e:
        return jsonify({"error": str(e), "picks": []}), 500

'@
    $app = $app.Replace('if __name__ == "__main__":', $slateEndpoint + 'if __name__ == "__main__":')
    [System.IO.File]::WriteAllText($appPath, $app, [System.Text.Encoding]::UTF8)
    Write-Host "  app.py patched OK" -ForegroundColor Green
}

# ── 3. Git commit and push ────────────────────────────────────────────────────
Write-Host ""
Write-Host "Committing and pushing..." -ForegroundColor Cyan
Push-Location $Root
git add ui_runner/app.py ui_runner/templates/index.html
git commit -m "feat: dynamic home page picks via /api/slate"
git push origin main
Pop-Location
Write-Host ""
Write-Host "Done! Railway will redeploy in ~60 seconds." -ForegroundColor Green
