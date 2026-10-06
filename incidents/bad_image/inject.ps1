param([ValidateRange(1, 300)][int]$TimeoutSeconds = 90)
. (Join-Path $PSScriptRoot 'common.ps1')
$null = Get-IncidentDeployment

# No registry access: this tag is deliberately absent from the kind node.
Invoke-IncidentKubectl @('set', 'image', 'deployment/api', "api=$brokenImage") | Out-Host
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    foreach ($pod in @(Get-IncidentPods)) {
        if ($pod.spec.containers[0].image -ne $brokenImage -or
            -not $pod.status.PSObject.Properties['containerStatuses']) { continue }
        foreach ($container in @($pod.status.containerStatuses)) {
            if ($container.name -ne 'api' -or -not $container.state.PSObject.Properties['waiting']) { continue }
            if ($container.state.waiting.reason -ne 'ErrImageNeverPull') { continue }
            $events = (Invoke-IncidentKubectl @('get', 'events', '--field-selector', "involvedObject.uid=$($pod.metadata.uid)", '-o', 'json') | Out-String) | ConvertFrom-Json
            if (@($events.items | Where-Object { $_.reason -eq 'ErrImageNeverPull' -and $_.type -eq 'Warning' }).Count -eq 0) { continue }
            Invoke-IncidentKubectl @('get', 'pods', '-l', 'app=api', '-o', 'wide') | Out-Host
            Invoke-IncidentKubectl @('get', 'events', '--field-selector', "involvedObject.uid=$($pod.metadata.uid)", '--sort-by=.lastTimestamp') | Out-Host
            Write-Host "M1 injected: $($pod.metadata.name) cannot start $brokenImage (ErrImageNeverPull)."
            Write-Host 'The previous healthy API replica can keep serving during the stalled rollout.'
            return
        }
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
throw "M1 injection did not produce ErrImageNeverPull and a matching Warning event within ${TimeoutSeconds}s. Inspect API pods/events; run incidents/bad_image/restore.ps1 to recover."
