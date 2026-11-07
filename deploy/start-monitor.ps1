# Starts monitor.ps1 as a separate process (survives across workflow steps)
# and waits until it has recorded at least one successful "before" response.
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$OutDir
)
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "monitor.ps1"
$p = Start-Process powershell.exe -PassThru -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"", "-Url", $Url, "-OutDir", "`"$OutDir`"")
Set-Content -Path (Join-Path $OutDir "monitor.pid") -Value $p.Id
Write-Host "monitor started (pid $($p.Id)) -> $Url/api/analyze"

$csv = Join-Path $OutDir "requests.csv"
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $csv) {
        $rows = @(Import-Csv $csv)
        if ($rows | Where-Object { $_.http -eq "200" }) { Write-Host "baseline response OK (revision $($rows[-1].revision))"; exit 0 }
    }
}
Write-Host "::warning::no successful baseline response yet (first deployment or service down) - continuing"
