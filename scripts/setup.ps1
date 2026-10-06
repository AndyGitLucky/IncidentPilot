# Run from any directory: powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$kindVersion = 'v0.33.0'
$clusterName = 'incidentpilot'
$contextName = "kind-$clusterName"
$localDir = Join-Path $repoRoot '.local'
$toolsDir = Join-Path $repoRoot '.tools'

function Invoke-Native {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE."
    }
}

foreach ($tool in @('docker', 'kubectl')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Install $tool before running setup; see README.md."
    }
}
$dockerType = & docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $dockerType -ne 'linux') {
    throw 'Start Docker Desktop with Linux containers, then rerun setup.'
}

New-Item -ItemType Directory -Force -Path $localDir, $toolsDir | Out-Null
$env:KUBECONFIG = Join-Path $localDir 'kubeconfig'

$runtime = [System.Runtime.InteropServices.RuntimeInformation]
if ($runtime::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
    $platformName = 'windows'
    $extension = '.exe'
} elseif ($runtime::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::OSX)) {
    $platformName = 'darwin'
    $extension = ''
} else {
    $platformName = 'linux'
    $extension = ''
}
$architecture = $runtime::OSArchitecture.ToString().ToLowerInvariant()
if ($architecture -eq 'x64') { $architecture = 'amd64' }
if ($architecture -notin @('amd64', 'arm64')) { throw "Unsupported CPU: $architecture" }
$assetName = "kind-$platformName-$architecture"
$kindPath = Join-Path $toolsDir "kind-$kindVersion$extension"
$checksumPath = "$kindPath.sha256sum"
$downloadUrl = "https://github.com/kubernetes-sigs/kind/releases/download/$kindVersion/$assetName"
if (-not (Test-Path -LiteralPath $kindPath) -or -not (Test-Path -LiteralPath $checksumPath)) {
    Write-Host "Downloading kind $kindVersion ($platformName/$architecture)..."
    Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $kindPath
    Invoke-WebRequest -UseBasicParsing -Uri "$downloadUrl.sha256sum" -OutFile $checksumPath
}
$expectedHash = ((Get-Content -Raw -LiteralPath $checksumPath).Trim() -split '\s+')[0]
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $kindPath).Hash
if ($expectedHash -ne $actualHash) { throw 'kind checksum mismatch; remove .tools kind files and retry.' }
if ($platformName -ne 'windows') { Invoke-Native 'chmod' @('+x', $kindPath) }
Invoke-Native $kindPath @('version')

$clusters = & $kindPath get clusters
if ($LASTEXITCODE -ne 0) { throw 'Could not list kind clusters.' }
if ($clusters -notcontains $clusterName) {
    Invoke-Native $kindPath @('create', 'cluster', '--name', $clusterName, '--config', (Join-Path $repoRoot 'cluster/kind.yaml'), '--wait', '180s')
} else {
    Invoke-Native $kindPath @('export', 'kubeconfig', '--name', $clusterName, '--kubeconfig', $env:KUBECONFIG)
}

foreach ($service in @('api', 'frontend')) {
    Invoke-Native 'docker' @('build', '--tag', "incidentpilot-${service}:m0", '--file', (Join-Path $repoRoot "app/$service/Dockerfile"), $repoRoot)
}
# Preload external images too: the node does not need its own registry access.
foreach ($image in @('postgres:17.5-alpine', 'prom/prometheus:v3.3.0')) {
    Invoke-Native 'docker' @('pull', $image)
}
Invoke-Native $kindPath @('load', 'docker-image', '--name', $clusterName, 'incidentpilot-api:m0', 'incidentpilot-frontend:m0', 'postgres:17.5-alpine', 'prom/prometheus:v3.3.0')

# The only out-of-base input is the tracked SQL seed in app/db.
$manifests = & kubectl kustomize (Join-Path $repoRoot 'cluster/base') --load-restrictor LoadRestrictionsNone
if ($LASTEXITCODE -ne 0) { throw 'Could not render demo manifests.' }
$manifests | & kubectl --context $contextName apply -f -
if ($LASTEXITCODE -ne 0) { throw 'Could not apply demo manifests.' }
# Rebuilt images retain the M0 tag; ensure an existing cluster starts new containers.
Invoke-Native 'kubectl' @('--context', $contextName, '-n', 'incidentpilot', 'rollout', 'restart', 'deployment/api', 'deployment/frontend')
foreach ($deployment in @('postgres', 'api', 'frontend', 'prometheus')) {
    Invoke-Native 'kubectl' @('--context', $contextName, '-n', 'incidentpilot', 'rollout', 'status', "deployment/$deployment", '--timeout=180s')
}
Invoke-Native 'kubectl' @('--context', $contextName, '-n', 'incidentpilot', 'get', 'pods')
& (Join-Path $PSScriptRoot 'verify.ps1')
Write-Host "Kubeconfig: $env:KUBECONFIG"
Write-Host 'Demo: http://localhost:8080 | Prometheus: http://localhost:9090'
