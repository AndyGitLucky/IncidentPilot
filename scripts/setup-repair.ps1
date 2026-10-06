# Manual provisioning only; the agent never uses the admin kubeconfig.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$adminConfig = Join-Path $repoRoot '.local/kubeconfig'
$repairConfig = Join-Path $repoRoot '.local/repair-kubeconfig'
if (-not (Test-Path -LiteralPath $adminConfig)) { throw 'Run scripts/setup.ps1 first.' }

function Invoke-RepairSetupKubectl {
    param([string[]]$Arguments)
    $output = & kubectl --kubeconfig $adminConfig --context kind-incidentpilot --namespace incidentpilot --request-timeout=10s @Arguments
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed with exit code $LASTEXITCODE." }
    return $output
}

Invoke-RepairSetupKubectl @('apply', '-f', (Join-Path $repoRoot 'cluster/repair-runner.yaml')) | Out-Host
$token = (Invoke-RepairSetupKubectl @('create', 'token', 'repair-runner', '--duration=1h') | Out-String).Trim()
$configuration = (Invoke-RepairSetupKubectl @('config', 'view', '--minify', '--raw', '-o', 'json') | Out-String) | ConvertFrom-Json
$configuration.users = @([PSCustomObject]@{name = 'repair-runner'; user = [PSCustomObject]@{token = $token}})
$configuration.contexts[0].context.user = 'repair-runner'
$configuration.contexts[0].context | Add-Member -NotePropertyName namespace -NotePropertyValue incidentpilot -Force
$json = $configuration | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($repairConfig, $json, (New-Object System.Text.UTF8Encoding($false)))
if ($env:OS -ne 'Windows_NT') {
    & chmod 600 $repairConfig
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict kubeconfig permissions.' }
}
Write-Host 'M3 separate repair credentials ready in .local/repair-kubeconfig (one-hour token).'
