# Blue/green zero-downtime deployment for the Rate-my-face service.
#
#   client -> 127.0.0.1:8000 -> rmf-proxy (nginx) -> rmf-app-blue | rmf-app-green
#
# 1. start the new image in the idle color (no host port, private network)
# 2. wait until its Docker HEALTHCHECK reports healthy
# 3. point nginx at the new container and reload gracefully (in-flight requests finish)
# 4. confirm the proxy now serves the new revision, then drain and remove the old color
# If anything fails before step 3, the old container keeps serving and the new one is removed.
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [string]$Network = "rmf-net",
    [string]$Proxy = "rmf-proxy",
    [int]$HostPort = 8000,
    [string]$MarkerDir = $env:MONITOR_DIR,
    [string]$ExpectedRevision = $env:DEPLOY_SHA
)
$ErrorActionPreference = "Stop"

function Log($msg) { Write-Host ("[deploy {0}] {1}" -f (Get-Date).ToUniversalTime().ToString("HH:mm:ss.fff"), $msg) }
function Mark($name) {
    if (-not $MarkerDir) { return }
    if (-not (Test-Path $MarkerDir)) { New-Item -ItemType Directory -Force -Path $MarkerDir | Out-Null }
    if ($MarkerDir) { Add-Content -Path (Join-Path $MarkerDir "markers.txt") -Value ("{0}={1}" -f $name, (Get-Date).ToUniversalTime().ToString("o")) }
}
# docker.exe (with extension) so this wrapper does not call itself recursively
function Docker { & docker.exe @args; if ($LASTEXITCODE -ne 0) { throw "docker $($args -join ' ') failed ($LASTEXITCODE)" } }
function Running($name) { (& docker ps -q --filter "name=^/$name$" --filter "status=running") -ne $null }
function Exists($name) { (& docker ps -aq --filter "name=^/$name$") -ne $null }

function Write-ProxyConf($target) {
    $conf = @"
server {
    listen 80;
    client_max_body_size 11m;
    location / {
        proxy_pass http://${target}:8000;
        proxy_http_version 1.1;
        proxy_set_header Host `$host;
        proxy_set_header X-Real-IP `$remote_addr;
        proxy_connect_timeout 3s;
        proxy_read_timeout 60s;
        proxy_next_upstream error timeout http_502 http_503;
    }
}
"@
    $tmp = Join-Path $env:TEMP "rmf-default.conf"
    [System.IO.File]::WriteAllText($tmp, ($conf -replace "`r`n", "`n"))
    return $tmp
}

Mark "deploy_start"

# --- network -----------------------------------------------------------------
if (-not (& docker network ls -q --filter "name=^$Network$")) { Log "create network $Network"; Docker network create $Network | Out-Null }

# --- decide colors -----------------------------------------------------------
$active = $null
foreach ($c in "blue", "green") { if (Running "rmf-app-$c") { $active = $c } }
$next = if ($active -eq "blue") { "green" } else { "blue" }
$newName = "rmf-app-$next"
Log "active color: $(if ($active) { $active } else { 'none' }) -> deploying $next ($Image)"

if (Exists $newName) { Log "remove stale $newName"; Docker rm -f $newName | Out-Null }

# --- start new color and wait for health -------------------------------------
Docker run -d --name $newName --network $Network --restart unless-stopped $Image | Out-Null
$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    $state = (& docker inspect -f "{{.State.Health.Status}}" $newName).Trim()
    if ($state -eq "healthy") { $healthy = $true; break }
    if ($state -eq "unhealthy") { break }
    Start-Sleep -Seconds 1
}
if (-not $healthy) {
    Log "new container did not become healthy; keeping $active"
    & docker logs --tail 50 $newName
    & docker rm -f $newName | Out-Null
    Mark "deploy_failed"
    throw "deployment aborted: $newName unhealthy"
}
Log "$newName is healthy"

# --- point proxy at the new color --------------------------------------------
$confFile = Write-ProxyConf $newName
if (-not (Running $Proxy)) {
    if (Exists $Proxy) { Docker rm -f $Proxy | Out-Null }
    Log "create proxy $Proxy on 127.0.0.1:$HostPort"
    Docker create --name $Proxy --network $Network --restart unless-stopped -p "127.0.0.1:${HostPort}:80" nginx:1.27-alpine | Out-Null
    Docker cp $confFile "${Proxy}:/etc/nginx/conf.d/default.conf"
    Docker start $Proxy | Out-Null
} else {
    Docker cp $confFile "${Proxy}:/etc/nginx/conf.d/default.conf"
    Docker exec $Proxy nginx -t
    Docker exec $Proxy nginx -s reload
}
Mark "switch"
Log "proxy now routes to $newName"

# --- confirm the public URL serves the new revision ---------------------------
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$HostPort/api/health" -TimeoutSec 3
        if (-not $ExpectedRevision -or $h.revision -eq $ExpectedRevision) { $ok = $true; Log "public health: version=$($h.version) revision=$($h.revision)"; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ok) { Mark "deploy_failed"; throw "proxy is not serving revision $ExpectedRevision" }

# --- drain and remove the old color ------------------------------------------
if ($active) {
    Start-Sleep -Seconds 5
    Log "stop old rmf-app-$active"
    Docker stop -t 15 "rmf-app-$active" | Out-Null
    Docker rm "rmf-app-$active" | Out-Null
}
Mark "deploy_end"
Log "deployment complete: $newName"
