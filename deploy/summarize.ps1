# Builds the zero-downtime verification report from requests.csv + markers.txt,
# prints every request to the job log and writes a one-screen GitHub Step Summary.
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$Url = "http://127.0.0.1:8000",
    [string]$ExpectedRevision = "",
    [double]$GapThresholdSec = 3.0
)
$ErrorActionPreference = "Continue"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$csv = Join-Path $OutDir "requests.csv"
if (-not (Test-Path $csv)) { Write-Host "::error::no monitoring data"; exit 1 }
$rows = @(Import-Csv $csv)
foreach ($r in $rows) {
    $r | Add-Member -NotePropertyName t0 -NotePropertyValue ([datetime]::Parse($r.start_utc).ToUniversalTime())
    $r | Add-Member -NotePropertyName t1 -NotePropertyValue ([datetime]::Parse($r.end_utc).ToUniversalTime())
}

# markers written by deploy.ps1
$m = @{}
$mf = Join-Path $OutDir "markers.txt"
if (Test-Path $mf) { Get-Content $mf | ForEach-Object { $k, $v = $_ -split "=", 2; $m[$k] = [datetime]::Parse($v).ToUniversalTime() } }
$dStart = $m["deploy_start"]; $dEnd = $m["deploy_end"]
if (-not $dEnd) { $dEnd = $m["deploy_failed"] }

function StageOf($r) {
    if ($dStart -and $r.t0 -lt $dStart) { return "Before" }
    if ($dEnd -and $r.t0 -gt $dEnd) { return "After" }
    return "During"
}
foreach ($r in $rows) { $r | Add-Member -NotePropertyName stage -NotePropertyValue (StageOf $r) }

# --- job log: every request ---------------------------------------------------
Write-Host "seq stage  start_utc                     ms    http ok    revision"
foreach ($r in $rows) {
    Write-Host ("{0,-3} {1,-6} {2} {3,5} {4,4} {5,-5} {6}" -f $r.seq, $r.stage, $r.start_utc, $r.latency_ms, $r.http, $r.analysis_ok, $r.revision)
}

# --- statistics ---------------------------------------------------------------
$total = $rows.Count
$good = @($rows | Where-Object { $_.http -eq "200" -and $_.analysis_ok -eq "True" })
$failed = $total - $good.Count
$gaps = @(); $intervals = @()
for ($i = 1; $i -lt $total; $i++) {
    $intervals += ($rows[$i].t0 - $rows[$i - 1].t1).TotalSeconds        # wait between response and next request
    $gaps += [pscustomobject]@{ from = $rows[$i - 1].seq; to = $rows[$i].seq; sec = ($rows[$i].t0 - $rows[$i - 1].t0).TotalSeconds }
}
$maxGap = if ($gaps.Count) { ($gaps | Sort-Object sec -Descending)[0] } else { $null }
$avgInterval = if ($intervals.Count) { [math]::Round(($intervals | Measure-Object -Average).Average, 2) } else { 0 }
$unverified = @($gaps | Where-Object { $_.sec -gt $GapThresholdSec })
$firstRev = ($rows | Where-Object { $_.revision } | Select-Object -First 1).revision
$lastRev = ($rows | Where-Object { $_.revision } | Select-Object -Last 1).revision
$firstVer = ($rows | Where-Object { $_.version } | Select-Object -First 1).version
$lastVer = ($rows | Where-Object { $_.version } | Select-Object -Last 1).version
$switched = (-not $ExpectedRevision) -or ($lastRev -eq $ExpectedRevision)
$result = if ($failed -eq 0 -and $switched -and -not $m["deploy_failed"]) { "SUCCESS" } else { "FAILURE" }
$winStart = if ($total) { $rows[0].t0.ToString("yyyy-MM-ddTHH:mm:ssZ") } else { "-" }
$winEnd = if ($total) { $rows[-1].t1.ToString("yyyy-MM-ddTHH:mm:ssZ") } else { "-" }
function Short($s) { if ($s -and $s.Length -gt 12) { $s.Substring(0, 12) } else { $s } }

# --- summary markdown ---------------------------------------------------------
$md = New-Object System.Text.StringBuilder
[void]$md.AppendLine("## Rate-my-face zero-downtime deployment verification")
[void]$md.AppendLine("")
[void]$md.AppendLine("**Result: $result** | URL: $Url/api/analyze | Window (UTC): $winStart -> $winEnd")
[void]$md.AppendLine("")
[void]$md.AppendLine("| Total requests | Success | Failed | Version | Revision | Avg wait after response | Largest gap between records |")
[void]$md.AppendLine("|---|---|---|---|---|---|---|")
$gapTxt = if ($maxGap) { "{0:N2}s (#{1} -> #{2})" -f $maxGap.sec, $maxGap.from, $maxGap.to } else { "-" }
[void]$md.AppendLine("| $total | $($good.Count) | $failed | $firstVer -> $lastVer | $(Short $firstRev) -> $(Short $lastRev) | ${avgInterval}s | $gapTxt |")
[void]$md.AppendLine("")
[void]$md.AppendLine("| Stage | Requests (ok/total) | Representative | UTC start | HTTP | Analysis | Latency | Revision | Score |")
[void]$md.AppendLine("|---|---|---|---|---|---|---|---|---|")
foreach ($s in "Before", "During", "After") {
    $sr = @($rows | Where-Object { $_.stage -eq $s })
    if (-not $sr.Count) { [void]$md.AppendLine("| $s | 0/0 | - | - | - | - | - | - | - |"); continue }
    $ok = @($sr | Where-Object { $_.http -eq "200" -and $_.analysis_ok -eq "True" }).Count
    $rep = $sr[[int][math]::Floor(($sr.Count - 1) / 2)]
    if ($s -eq "Before") { $rep = $sr[-1] } elseif ($s -eq "After") { $rep = $sr[-1] }
    [void]$md.AppendLine("| $s | $ok/$($sr.Count) | #$($rep.seq) | $($rep.t0.ToString('HH:mm:ss.fff')) | $($rep.http) | $($rep.analysis_ok) | $($rep.latency_ms) ms | $(Short $rep.revision) | $($rep.score) |")
}
[void]$md.AppendLine("")
if ($unverified.Count) {
    $list = ($unverified | ForEach-Object { "#{0}->#{1} ({2:N1}s)" -f $_.from, $_.to, $_.sec }) -join ", "
    [void]$md.AppendLine("Unverified periods (gap > ${GapThresholdSec}s, no observation): $list")
} else {
    [void]$md.AppendLine("No unverified periods: every gap between records was <= ${GapThresholdSec}s.")
}
if ($failed -eq 0) {
    [void]$md.AppendLine("Within the observed request window, every analyze request succeeded while the container was replaced -> no service interruption observed.")
} else {
    [void]$md.AppendLine("$failed request(s) failed during the observed window - see requests.csv artifact.")
}
if ($dStart) { [void]$md.AppendLine("Deploy markers (UTC): start $($dStart.ToString('HH:mm:ss')), switch $(if ($m['switch']) { $m['switch'].ToString('HH:mm:ss') } else { '-' }), end $(if ($dEnd) { $dEnd.ToString('HH:mm:ss') } else { '-' })") }

$text = $md.ToString()
[System.IO.File]::WriteAllText((Join-Path $OutDir "summary.md"), $text, $utf8)
if ($env:GITHUB_STEP_SUMMARY) { [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, $text, $utf8) }
Write-Host $text
if ($result -ne "SUCCESS") { exit 1 }
