# Live M2 acceptance: healthy -> manual injection -> diagnosis -> manual restore.
# This invokes OpenRouter three times with bounded demo state, events, and API logs.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$adminConfig = Join-Path $repoRoot '.local/kubeconfig'
$readerConfig = Join-Path $repoRoot '.local/diagnosis-kubeconfig'
$artifactDir = Join-Path $repoRoot '.local/m2'
if (-not (Test-Path -LiteralPath $readerConfig)) { throw 'Run scripts/setup-diagnosis.ps1 first.' }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv; see README.md.' }

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

function Assert-ReaderPermission {
    param([string[]]$Arguments, [string]$Expected)
    $response = & kubectl --kubeconfig $readerConfig --context kind-incidentpilot --namespace incidentpilot --request-timeout=10s auth can-i @Arguments
    $permissionExit = $LASTEXITCODE
    if (($response | Out-String).Trim() -ne $Expected -or $permissionExit -gt 1) {
        throw "Unexpected diagnosis-reader permission: $($Arguments -join ' ')"
    }
}

function Assert-Diagnosis {
    param([string]$Label, [string]$ExpectedStatus)
    $before = Get-DemoSignature
    $artifact = Join-Path $artifactDir "$Label.json"
    & uv run --frozen --extra agent --project $repoRoot python -m agent --output $artifact | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Diagnosis failed; inspect $artifact." }
    if ($before -ne (Get-DemoSignature)) { throw 'Demo resource identities, generations, or specifications changed during read-only diagnosis.' }
    $report = Get-Content -Raw -Encoding UTF8 -LiteralPath $artifact | ConvertFrom-Json
    if (($report.stages -join ',') -ne 'COLLECT,HYPOTHESIZE' -or $report.diagnosis.status -ne $ExpectedStatus) {
        throw "Unexpected $Label diagnosis; inspect $artifact."
    }
    if ($report.model_diagnosis.status -ne $report.diagnosis.status) { throw 'Model conclusion did not pass evidence validation.' }
    if ($report.usage.total_tokens -le 0) { throw 'Expected a live LLM response with token usage.' }
    foreach ($citation in $report.diagnosis.evidence_ids) {
        if (@($report.evidence | Where-Object { $_.id -eq $citation -and -not $_.error }).Count -ne 1) { throw "Invalid evidence citation: $citation" }
    }
    if (@($report.evidence | Where-Object { $_.error }).Count -gt 0) { throw 'Live acceptance requires all evidence tools to succeed.' }
    if ($ExpectedStatus -eq 'diagnosed' -and $report.diagnosis.incident_type -ne 'bad_image') { throw 'Expected bad-image root cause.' }
    Write-Host "M2 ${Label}: $ExpectedStatus; citations $($report.diagnosis.evidence_ids -join ', '); $($report.usage.total_tokens) tokens."
}

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
    # Check configuration before intentionally breaking the fixture. Does not call the LLM.
    & uv run --frozen --extra agent python -c 'from agent.diagnosis import OpenRouterDiagnoser; OpenRouterDiagnoser.from_env()'
    if ($LASTEXITCODE -ne 0) { throw 'Configure OPENROUTER_API_KEY in .env first.' }
    Assert-ReaderPermission @('get', 'pods') 'yes'
    Assert-ReaderPermission @('get', 'pods/log') 'yes'
    Assert-ReaderPermission @('get', 'events') 'yes'
    Assert-ReaderPermission @('get', 'deployments.apps') 'yes'
    Assert-ReaderPermission @('patch', 'deployments.apps') 'no'
    Assert-ReaderPermission @('create', 'pods/exec') 'no'
    Assert-ReaderPermission @('get', 'secrets') 'no'
    Assert-ReaderPermission @('delete', 'persistentvolumeclaims') 'no'
    & (Join-Path $PSScriptRoot 'verify.ps1')
    Assert-Diagnosis 'healthy' 'no_active_incident'
    try {
        & (Join-Path $repoRoot 'incidents/bad_image/inject.ps1')
        Assert-Diagnosis 'injected' 'diagnosed'
    } finally {
        & (Join-Path $repoRoot 'incidents/bad_image/restore.ps1')
    }
    Assert-Diagnosis 'restored' 'no_active_incident'
    Write-Host "M2 acceptance passed. Reports: $artifactDir. Demo left healthy."
} finally {
    Pop-Location
}
