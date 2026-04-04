# GitHub Pages Deployment Guide
## NBA-Pipelines → Live Website

Your pipeline already generates everything needed. This guide wires it to GitHub Pages so every run auto-publishes to a live URL.

---

## Step 1 — One-time GitHub Setup

### Enable GitHub Pages
1. Go to your repo on GitHub → **Settings** → **Pages**
2. Under **Source**, select:
   - Branch: `main` (or your default branch)
   - Folder: `/docs`
3. Click **Save**
4. GitHub gives you a URL like: `https://halek.github.io/NBA-Pipelines/`

### Make sure `docs/` is tracked
In your repo root, check `.gitignore` — make sure `docs/` is **not** in it.

---

## Step 2 — Update `commands.json` (already done in this patch)

The `combined_run` command now includes `--write-web --web-outdir docs --also-root`.

This means every time you run "Build Combined Slate + Tickets", it writes:
- `docs/tickets_latest.html`  ← what GitHub Pages serves
- `docs/tickets_latest.json`  ← raw data
- `tickets_latest.html`       ← root copy (for Flask local server)
- `tickets_latest.json`       ← root copy

---

## Step 3 — After every pipeline run, push to GitHub

Run this in PowerShell from your repo root:

```powershell
cd "C:\Users\halek\OneDrive\Desktop\Vision Board\NbaPropPipelines\NBA-Pipelines"
git add docs/tickets_latest.html docs/tickets_latest.json
git add tickets_latest.html tickets_latest.json
git commit -m "chore: update tickets $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git push origin main
```

GitHub Pages deploys automatically within ~60 seconds of the push.

---

## Step 4 — Automate the push (optional but recommended)

Add this to the bottom of `run_pipeline.ps1` to auto-push after every full run:

```powershell
# ── Auto-push docs to GitHub Pages ──────────────────────────────────────────
if ($NBASuccess -and $CBBSuccess) {
    Write-Host "[ GIT ] Pushing docs to GitHub Pages..." -ForegroundColor Cyan
    Push-Location $Root
    git add docs/tickets_latest.html docs/tickets_latest.json `
            tickets_latest.html tickets_latest.json 2>&1 | Out-Null
    $msg = "chore: pipeline update $Date $(Get-Date -Format 'HH:mm')"
    git commit -m $msg 2>&1 | Out-Null
    git push origin main 2>&1
    Pop-Location
    Write-Host "  Pushed → GitHub Pages" -ForegroundColor Green
}
```

---

## Step 5 — Fix the hardcoded path in `run_pipeline.ps1`

Replace line 25:
```powershell
# OLD (breaks on any machine other than yours):
$Root = "C:\Users\halek\OneDrive\Desktop\Vision Board\NbaPropPipelines\NBA-Pipelines"

# NEW (auto-resolves to wherever the script lives):
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
```

Also update `commands.json` — change `"repo_root"` to use a relative marker:
```json
"repo_root": "."
```
Then in `app.py`, resolve it relative to `commands.json`'s location (already handled if `BASE_DIR` points to `ui_runner/`'s parent).

---

## What your live site looks like

After your first push with `--write-web`, visiting your GitHub Pages URL shows:
- All tickets for today in clean card layout
- NBA + CBB + Combined tabs
- Player headshots where IDs exist
- Auto-refreshes next day when you push again

The URL to share: `https://YOUR_GITHUB_USERNAME.github.io/NBA-Pipelines/tickets_latest.html`

---

## Summary of all files changed in this patch

| File | What changed |
|------|-------------|
| `commands.json` | `combined_run` now writes web outputs; `full_pipeline` added `-AutoPush` flag |
| `step9_build_tickets.py` | Greedy builder → combinatorial optimizer; `min_hit_rate` bug fixed |
| `cbb_step6_rank_props.py` | Aligned with NBA: prop weights, Bayesian prior, D1 defense rank scale fixed, consistent reliability_mult |
| `run_pipeline.ps1` | Hardcoded path fix + optional auto-push block |
| `GITHUB_DEPLOY.md` | This file |
