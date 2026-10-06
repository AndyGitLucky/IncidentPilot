# Provision short-lived read-only credentials using the manual setup kubeconfig.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$adminConfig = Join-Path $repoRoot '.local/kubeconfig'
$readerConfig = Join-Path $repoRoot '.local/diagnosis-kubeconfig'
if (-not (Test-Path -LiteralPath $adminConfig)) { throw 'Run scripts/setup.ps1 first.' }

function Invoke-DiagnosisSetupKubectl {
    param([string[]]$Arguments)
    $output = & kubectl --kubeconfig $adminConfig --context kind-incidentpilot --namespace incidentpilot --request-timeout=10s @Arguments
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed with exit code $LASTEXITCODE." }
    return $output
}

Invoke-DiagnosisSetupKubectl @('apply', '-f', (Join-Path $repoRoot 'cluster/diagnosis-reader.yaml')) | Out-Host
$token = (Invoke-DiagnosisSetupKubectl @('create', 'token', 'diagnosis-reader', '--duration=1h') | Out-String).Trim()
$configuration = (Invoke-DiagnosisSetupKubectl @('config', 'view', '--minify', '--raw', '-o', 'json') | Out-String) | ConvertFrom-Json
# Copy only cluster TLS/address information; never copy admin credentials.
$configuration.users = @([PSCustomObject]@{name = 'diagnosis-reader'; user = [PSCustomObject]@{token = $token}})
$configuration.contexts[0].context.user = 'diagnosis-reader'
$configuration.contexts[0].context | Add-Member -NotePropertyName namespace -NotePropertyValue incidentpilot -Force
$json = $configuration | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($readerConfig, $json, (New-Object System.Text.UTF8Encoding($false)))
if ($env:OS -ne 'Windows_NT') {
    & chmod 600 $readerConfig
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict kubeconfig permissions.' }
}
Write-Host 'M2 read-only credentials ready in .local/diagnosis-kubeconfig (one-hour token).'
Write-Host 'Rerun this script to refresh expired credentials. The agent never uses the admin kubeconfig.'
