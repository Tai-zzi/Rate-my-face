param([Parameter(Mandatory = $true)][string]$OutDir)
Set-Content -Path (Join-Path $OutDir "stop.flag") -Value "stop"
$pidFile = Join-Path $OutDir "monitor.pid"
if (Test-Path $pidFile) {
    $id = [int](Get-Content $pidFile)
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) { if (-not $p.WaitForExit(20000)) { Stop-Process -Id $id -Force } }
}
Write-Host "monitor stopped"
