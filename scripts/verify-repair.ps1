# Live M3 acceptance. Two OpenRouter calls; the second repair requires human input.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$adminConfig = Join-Path $repoRoot '.local/kubeconfig'
$repairConfig = Join-Path $repoRoot '.local/repair-kubeconfig'
$readerConfig = Join-Path $repoRoot '.local/diagnosis-kubeconfig'
$artifactDir = Join-Path $repoRoot '.local/m3'
foreach ($path in @($repairConfig, $readerConfig)) {
    if (-not (Test-Path -LiteralPath $path)) { throw 'Run setup-diagnosis.ps1 and setup-repair.ps1 first.' }
}

function Get-DemoSignature {
    $output = & kubectl --kubeconfig $adminConfig --context kind-incidentpilot --namespace incidentpilot --request-timeout=10s get deployments,services,pods,persistentvolumeclaims -o json
    if ($LASTEXITCODE -ne 0) { throw 'Could not snapshot demo resources.' }
    $resources = ($output | Out-String) | ConvertFrom-Json
    $items = @($resources.items | Where-Object { -not $_.metadata.PSObject.Properties['deletionTimestamp'] } |
        Sort-Object @{Expression = {$_.kind}}, @{Expression = {$_.metadata.name}} | ForEach-Object {
            $generation = if ($_.metadata.PSObject.Properties['generation']) { $_.metadata.generation } else { $null }
            [PSCustomObject]@{kind = $_.kind; name = $_.metadata.name; uid = $_.metadata.uid; generation = $generation; spec = $_.spec}
        })
    return ($items | ConvertTo-Json -Depth 40 -Compress)
}

function Assert-Permission {
    param([string]$Config, [string[]]$Arguments, [string]$Expected)
    $response = & kubectl --kubeconfig $Config --context kind-incidentpilot --namespace incidentpilot --request-timeout=10s auth can-i @Arguments
    if (($response | Out-String).Trim() -ne $Expected -or $LASTEXITCODE -gt 1) {
        throw "Unexpected permission: $($Arguments -join ' ')"
    }
}

function Get-UnrelatedDemoSignature {
    param([string]$Snapshot)
    # Windows PowerShell's ConvertFrom-Json emits a JSON array as one object.
    # Assign it first so the next pipeline enumerates each resource before filtering.
    $resources = $Snapshot | ConvertFrom-Json
    $items = @($resources | Where-Object {
        $_.kind -ne 'Pod' -or $_.spec.containers[0].name -ne 'api'
    } | Where-Object { $_.kind -ne 'Deployment' -or $_.name -ne 'api' })
    return (ConvertTo-Json -InputObject $items -Depth 40 -Compress)
}

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
    & uv run --frozen --extra agent python -c 'from agent.diagnosis import OpenRouterDiagnoser; OpenRouterDiagnoser.from_env()'
    if ($LASTEXITCODE -ne 0) { throw 'Configure OPENROUTER_API_KEY in .env first.' }
    Assert-Permission $readerConfig @('patch', 'deployment/api') 'no'
    Assert-Permission $repairConfig @('patch', 'deployment/api') 'yes'
    Assert-Permission $repairConfig @('patch', 'deployment/frontend') 'no'
    Assert-Permission $repairConfig @('patch', 'service/api') 'yes'
    Assert-Permission $repairConfig @('patch', 'service/frontend') 'no'
    Assert-Permission $readerConfig @('patch', 'service/api') 'no'
    Assert-Permission $repairConfig @('get', 'secrets') 'no'
    Assert-Permission $repairConfig @('create', 'pods/exec') 'no'
    Assert-Permission $repairConfig @('delete', 'persistentvolumeclaims') 'no'
    & (Join-Path $PSScriptRoot 'verify.ps1')
    try {
        & (Join-Path $repoRoot 'incidents/bad_image/inject.ps1')
        $before = Get-DemoSignature
        [System.IO.File]::WriteAllText((Join-Path $artifactDir 'before.json'), $before, (New-Object System.Text.UTF8Encoding($false)))
        $deniedPath = Join-Path $artifactDir 'denied.json'
        'deny' | & uv run --frozen --extra agent python -m agent --repair --output $deniedPath | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Denied proposal failed; inspect $deniedPath." }
        if ($before -ne (Get-DemoSignature)) { throw 'Resources changed without approval.' }
        $denied = Get-Content -Raw -Encoding UTF8 -LiteralPath $deniedPath | ConvertFrom-Json
        if ($denied.outcome -ne 'denied' -or $denied.approval -ne 'denied' -or -not $denied.proposal -or
            $denied.stages -contains 'EXECUTE' -or $denied.stages -contains 'VERIFY') { throw 'Approval denial failed.' }
        Write-Host 'M3 denied: cited fixed-image proposal; demo resource identities and specs unchanged.'
        $approvedPath = Join-Path $artifactDir 'approved.json'
        # No automatic approval: read the concrete proposal and type its exact approval phrase.
        & uv run --frozen --extra agent python -m agent --repair --output $approvedPath
        if ($LASTEXITCODE -ne 0) { throw "Approved repair failed; inspect $approvedPath." }
        $approved = Get-Content -Raw -Encoding UTF8 -LiteralPath $approvedPath | ConvertFrom-Json
        if ($approved.outcome -ne 'recovered' -or $approved.approval -ne 'approved' -or
            -not $approved.verification.healthy -or
            ($approved.stages -join ',') -ne 'COLLECT,HYPOTHESIZE,TEST,PROPOSE_FIX,WAIT_FOR_APPROVAL,EXECUTE,VERIFY,REPORT') {
            throw 'Human-approved recovery was not confirmed.'
        }
        # Preserve all non-API workloads, services, and volumes.
        $after = Get-DemoSignature
        [System.IO.File]::WriteAllText((Join-Path $artifactDir 'after.json'), $after, (New-Object System.Text.UTF8Encoding($false)))
        if ((Get-UnrelatedDemoSignature $before) -ne (Get-UnrelatedDemoSignature $after)) {
            throw 'Unrelated workloads, services, or volumes changed.'
        }
        & (Join-Path $PSScriptRoot 'verify.ps1')
        Write-Host "M3 acceptance passed. Reports: $artifactDir. Demo left healthy."
    } finally {
        # Manual test cleanup even if the human denies or execution fails.
        & (Join-Path $repoRoot 'incidents/bad_image/restore.ps1')
    }
} finally {
    Pop-Location
}
