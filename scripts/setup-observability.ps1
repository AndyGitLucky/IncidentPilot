# Optional M5 local stack. Run before a benchmark, never during preservation checks.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$adminConfig = Join-Path $repoRoot '.local/kubeconfig'
$localDir = Join-Path $repoRoot '.local/observability'
New-Item -ItemType Directory -Force -Path $localDir | Out-Null
$kubectlArgs = @('--kubeconfig', $adminConfig, '--context', 'kind-incidentpilot')
Push-Location $repoRoot
try {
    $manifest = & kubectl kustomize cluster/observability --load-restrictor LoadRestrictionsNone
    if ($LASTEXITCODE -ne 0) { throw 'Observability manifests failed to render.' }
    $manifest | & kubectl @kubectlArgs apply -f -
    if ($LASTEXITCODE -ne 0) { throw 'Observability stack could not be applied.' }
    & kubectl @kubectlArgs -n incidentpilot apply -f cluster/base/prometheus.yaml
    if ($LASTEXITCODE -ne 0) { throw 'Prometheus scrape configuration update failed.' }
    & kubectl @kubectlArgs -n incidentpilot rollout restart deployment/prometheus
    if ($LASTEXITCODE -ne 0) { throw 'Prometheus reload failed.' }
    & kubectl @kubectlArgs -n incidentpilot rollout status deployment/prometheus --timeout=180s
    if ($LASTEXITCODE -ne 0) { throw 'Prometheus did not become ready.' }
    foreach ($service in @('pushgateway', 'tempo', 'grafana')) {
        & kubectl @kubectlArgs -n incidentpilot-observability rollout status "deployment/$service" --timeout=180s
        if ($LASTEXITCODE -ne 0) { throw "$service did not become ready." }
    }
    $forwards = @(
        @{Name='grafana'; Ports='13000:3000'; Health='http://127.0.0.1:13000/api/health'},
        @{Name='tempo'; Ports='14318:4318'; Health=$null},
        @{Name='tempo-query'; Target='tempo'; Ports='13200:3200'; Health='http://127.0.0.1:13200/ready'},
        @{Name='pushgateway'; Ports='19091:9091'; Health='http://127.0.0.1:19091/-/ready'}
    )
    foreach ($forward in $forwards) {
        $pidFile = Join-Path $localDir "$($forward.Name).pid"
        # Reuse only a still-running helper recorded by this setup.
        if (Test-Path -LiteralPath $pidFile) {
            $savedPid = [int](Get-Content -LiteralPath $pidFile)
            $existing = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
            if ($existing -and $existing.ProcessName -eq 'kubectl') { continue }
        }
        $target = $forward.Name
        if ($forward.Target) { $target = $forward.Target }
        $processArguments = @('--kubeconfig', ('"' + $adminConfig + '"'), '--context', 'kind-incidentpilot',
            '-n', 'incidentpilot-observability', 'port-forward', "service/$target", $forward.Ports, '--address', '127.0.0.1')
        $startOptions = @{
            FilePath = 'kubectl'; ArgumentList = $processArguments; PassThru = $true;
            RedirectStandardOutput = (Join-Path $localDir "$($forward.Name).out.log");
            RedirectStandardError = (Join-Path $localDir "$($forward.Name).err.log")
        }
        if ($env:OS -eq 'Windows_NT') { $startOptions.WindowStyle = 'Hidden' }
        $process = Start-Process @startOptions
        $process.Id | Set-Content -LiteralPath $pidFile
    }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            foreach ($forward in $forwards) {
                if ($forward.Health) { Invoke-WebRequest -UseBasicParsing -Uri $forward.Health -TimeoutSec 2 | Out-Null }
            }
            # OTLP receiver rejects an empty payload with 400; connection refusal must fail.
            $otlpPort = New-Object System.Net.Sockets.TcpClient
            try { $otlpPort.Connect('127.0.0.1', 14318) } finally { $otlpPort.Dispose() }
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'Local port forwards are unavailable; inspect .local/observability/*.log.' }
    Write-Host 'Grafana: http://127.0.0.1:13000/d/incidentpilot/incidentpilot'
    Write-Host 'Run scripts/benchmark.ps1 -Observability to publish local traces and metrics.'
} finally {
    Pop-Location
}
