# Independent monitor: sends the fixed test image to the real analyze API,
# waits 1 second after each response, and records every request as one CSV row.
# Runs until <OutDir>\stop.flag appears.
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$OutDir
)
$ErrorActionPreference = "Continue"
$csv = Join-Path $OutDir "requests.csv"
$img = Join-Path $OutDir "test-face.jpg"
$stop = Join-Path $OutDir "stop.flag"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($csv, "seq,start_utc,end_utc,latency_ms,http,analysis_ok,version,revision,score,error`n", $utf8)

# Keep the machine awake while monitoring (like a media player; released when this process exits).
# A laptop entering Modern Standby during deploy #3 created a 102 s unobserved gap.
try {
    Add-Type -Namespace Native -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    [void][Native.Power]::SetThreadExecutionState([uint32]"0x80000003")
} catch { }

$seq = 0
while (-not (Test-Path $stop)) {
    $seq++
    $body = Join-Path $OutDir "last-response.json"
    $start = (Get-Date).ToUniversalTime()
    $code = & curl.exe -s -o $body -w "%{http_code}" --max-time 15 -F "file=@$img;type=image/jpeg" "$Url/api/analyze"
    $end = (Get-Date).ToUniversalTime()
    $ok = $false; $ver = ""; $rev = ""; $score = ""; $err = ""
    try {
        $d = [System.IO.File]::ReadAllText($body, $utf8) | ConvertFrom-Json
        $ok = [bool]$d.ok; $ver = $d.version; $rev = $d.revision; $score = $d.total_score
        if (-not $ok) { $err = $d.code }
    } catch { $err = "no-json" }
    if (-not $code -or $code -eq "000") { $code = "000"; $err = "connection-failed" }
    $line = "{0},{1},{2},{3},{4},{5},{6},{7},{8},{9}`n" -f $seq, $start.ToString("o"), $end.ToString("o"),
        [int]($end - $start).TotalMilliseconds, $code, $ok, $ver, $rev, $score, $err
    [System.IO.File]::AppendAllText($csv, $line, $utf8)
    Start-Sleep -Seconds 1
}
