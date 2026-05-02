#requires -Version 5.1
param(
    [string]$Label = "snapshot",
    [switch]$CompareToState,
    [switch]$WriteState = $true
)

$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent

$CacheDir = Join-Path $Root "data\cache"
$LogsDir = Join-Path $Root "logs"
if (-not (Test-Path $CacheDir)) { New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null }
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }

$StateFile = Join-Path $CacheDir "prop_snapshot_state.json"
$LogFile = Join-Path $LogsDir ("prop_snapshot_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-SnapLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    $line | Tee-Object -FilePath $LogFile -Append | Out-Null
}

function Get-RowSignature([pscustomobject]$Row) {
    $ordered = $Row.PSObject.Properties.Name | Sort-Object
    $parts = foreach ($k in $ordered) {
        $v = [string]$Row.$k
        "{0}={1}" -f $k, ($v -replace "\s+", " ").Trim()
    }
    return ($parts -join "|")
}

function Get-RowDisplay([pscustomobject]$Row) {
    $player = if ($Row.player_name) { $Row.player_name } elseif ($Row.player) { $Row.player } else { "UNKNOWN_PLAYER" }
    $stat = if ($Row.stat_type) { $Row.stat_type } elseif ($Row.prop_type) { $Row.prop_type } elseif ($Row.prop) { $Row.prop } else { "UNKNOWN_PROP" }
    $line = if ($Row.line_score) { $Row.line_score } elseif ($Row.line) { $Row.line } elseif ($Row.value) { $Row.value } else { "-" }
    $side = if ($Row.direction) { $Row.direction } elseif ($Row.side) { $Row.side } elseif ($Row.pick) { $Row.pick } else { "-" }
    return ("{0} | {1} | {2} | {3}" -f $player, $stat, $line, $side)
}

function Get-PropSnapshot([string]$CsvPath) {
    $sigSet = New-Object 'System.Collections.Generic.HashSet[string]'
    $displayBySig = @{}
    if (-not (Test-Path $CsvPath)) {
        return @{
            Count = 0
            Signatures = @()
            DisplayBySig = @{}
        }
    }
    $rows = Import-Csv -Path $CsvPath
    foreach ($r in $rows) {
        $sig = Get-RowSignature -Row $r
        if ($sigSet.Add($sig)) {
            $displayBySig[$sig] = Get-RowDisplay -Row $r
        }
    }
    return @{
        Count = $sigSet.Count
        Signatures = @($sigSet)
        DisplayBySig = $displayBySig
    }
}

function Get-Diff([string[]]$Before, [string[]]$After) {
    $beforeSet = New-Object 'System.Collections.Generic.HashSet[string]'
    $afterSet = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($s in $Before) { [void]$beforeSet.Add($s) }
    foreach ($s in $After) { [void]$afterSet.Add($s) }
    $added = New-Object 'System.Collections.Generic.List[string]'
    $removed = New-Object 'System.Collections.Generic.List[string]'
    foreach ($s in $afterSet) {
        if (-not $beforeSet.Contains($s)) { [void]$added.Add($s) }
    }
    foreach ($s in $beforeSet) {
        if (-not $afterSet.Contains($s)) { [void]$removed.Add($s) }
    }
    return @{
        Added = @($added)
        Removed = @($removed)
    }
}

$mlbStep1Snap = Join-Path $Root "Sports\MLB\data\outputs\step1_mlb_props.csv"
if (-not (Test-Path $mlbStep1Snap)) { $mlbStep1Snap = Join-Path $Root "Sports\MLB\step1_mlb_props.csv" }
$sportFiles = @{
    NBA = Join-Path $Root "Sports\NBA\data\outputs\step1_pp_props_today.csv"
    NHL = Join-Path $Root "Sports\NHL\outputs\step1_nhl_props.csv"
    Soccer = Join-Path $Root "Sports\Soccer\outputs\step1_soccer_props.csv"
    MLB = $mlbStep1Snap
}

$current = @{}
foreach ($sport in $sportFiles.Keys) {
    $snap = Get-PropSnapshot -CsvPath $sportFiles[$sport]
    $current[$sport] = @{
        count = $snap.Count
        signatures = $snap.Signatures
        displayBySig = $snap.DisplayBySig
    }
}

Write-SnapLog "=== PROP SNAPSHOT ($Label) ==="
foreach ($sport in @("NBA", "NHL", "Soccer", "MLB")) {
    Write-SnapLog ("{0}: {1} unique rows" -f $sport, $current[$sport].count)
}

$prior = $null
if (Test-Path $StateFile) {
    try {
        $prior = Get-Content -Path $StateFile -Raw | ConvertFrom-Json -AsHashtable
    }
    catch {
        Write-SnapLog "WARN: Could not parse prior state file. Starting fresh."
    }
}

if ($CompareToState -and $prior -and $prior.ContainsKey("sports")) {
    Write-SnapLog "--- Delta vs previous snapshot ---"
    foreach ($sport in @("NBA", "NHL", "Soccer", "MLB")) {
        $before = @()
        if ($prior.sports.ContainsKey($sport) -and $prior.sports[$sport].ContainsKey("signatures")) {
            $before = @($prior.sports[$sport].signatures)
        }
        $after = @($current[$sport].signatures)
        $diff = Get-Diff -Before $before -After $after
        Write-SnapLog ("{0}: +{1} added, -{2} removed" -f $sport, $diff.Added.Count, $diff.Removed.Count)

        $sampleAdded = @($diff.Added | Select-Object -First 5)
        foreach ($sig in $sampleAdded) {
            $disp = if ($current[$sport].displayBySig.ContainsKey($sig)) { $current[$sport].displayBySig[$sig] } else { $sig }
            Write-SnapLog ("  + {0}" -f $disp)
        }
        $sampleRemoved = @($diff.Removed | Select-Object -First 5)
        foreach ($sig in $sampleRemoved) {
            $disp = $sig
            if ($prior.sports[$sport].ContainsKey("displayBySig") -and $prior.sports[$sport].displayBySig.ContainsKey($sig)) {
                $disp = $prior.sports[$sport].displayBySig[$sig]
            }
            Write-SnapLog ("  - {0}" -f $disp)
        }
    }
}

if ($WriteState) {
    $statePayload = @{
        captured_at = (Get-Date).ToString("s")
        label = $Label
        sports = @{
            NBA = $current.NBA
            NHL = $current.NHL
            Soccer = $current.Soccer
            MLB = $current.MLB
        }
    }
    $statePayload | ConvertTo-Json -Depth 8 | Set-Content -Path $StateFile -Encoding UTF8
    Write-SnapLog "State saved -> $StateFile"
}

exit 0
