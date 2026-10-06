# Fixed M1 fixture, shared by the two manual incident scripts.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$kubeconfigPath = Join-Path $repoRoot '.local/kubeconfig'
$healthyImage = 'incidentpilot-api:m0'
$brokenImage = 'incidentpilot-api:m1-does-not-exist'

if (-not (Test-Path -LiteralPath $kubeconfigPath)) {
    throw 'Demo kubeconfig missing. Run scripts/setup.ps1 first.'
}
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw 'kubectl is required; see README.md.'
}

function Invoke-IncidentKubectl {
    param([string[]]$Arguments)
    $output = & kubectl --kubeconfig $kubeconfigPath --context kind-incidentpilot --namespace incidentpilot --request-timeout=10s @Arguments
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed with exit code $LASTEXITCODE." }
    return $output
}

function Get-IncidentDeployment {
    $deployment = (Invoke-IncidentKubectl @('get', 'deployment', 'api', '-o', 'json') | Out-String) | ConvertFrom-Json
    $containers = @($deployment.spec.template.spec.containers)
    if ($containers.Count -ne 1 -or $containers[0].name -ne 'api' -or
        $containers[0].image -notin @($healthyImage, $brokenImage) -or
        $containers[0].imagePullPolicy -ne 'Never' -or $deployment.spec.replicas -ne 1) {
        throw 'API deployment does not match the M1 fixture. Inspect it or rerun scripts/setup.ps1.'
    }
    return $deployment
}

function Get-IncidentPods {
    $pods = (Invoke-IncidentKubectl @('get', 'pods', '-l', 'app=api', '-o', 'json') | Out-String) | ConvertFrom-Json
    # Terminating pods are not evidence of the current rollout.
    return @($pods.items | Where-Object { -not $_.metadata.PSObject.Properties['deletionTimestamp'] })
}
