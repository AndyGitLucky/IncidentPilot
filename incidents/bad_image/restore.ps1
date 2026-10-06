param([ValidateRange(1, 300)][int]$TimeoutSeconds = 90)
. (Join-Path $PSScriptRoot 'common.ps1')
$null = Get-IncidentDeployment

# Restore the known baseline, independent of rollout revision/history.
Invoke-IncidentKubectl @('set', 'image', 'deployment/api', "api=$healthyImage") | Out-Host
Invoke-IncidentKubectl @('rollout', 'status', 'deployment/api', "--timeout=${TimeoutSeconds}s") | Out-Host
$deployment = Get-IncidentDeployment
if ($deployment.spec.template.spec.containers[0].image -ne $healthyImage -or
    $deployment.status.observedGeneration -lt $deployment.metadata.generation -or
    $deployment.status.updatedReplicas -ne 1 -or $deployment.status.readyReplicas -ne 1 -or
    $deployment.status.availableReplicas -ne 1 -or $deployment.status.replicas -ne 1) {
    throw 'API rollout did not return to exactly one ready baseline replica.'
}
foreach ($pod in @(Get-IncidentPods)) {
    if ($pod.spec.containers[0].image -ne $healthyImage) { throw 'A non-baseline API pod is still active.' }
}
& (Join-Path $repoRoot 'scripts/verify.ps1')
Write-Host "M1 restored: deployment/api uses $healthyImage; rollout, database-backed HTTP, and Prometheus checks passed."
