# Verify the public frontend path, real database response, and scraped metric.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$deadline = (Get-Date).AddSeconds(90)
$lastFailure = 'No response yet'
do {
    try {
        $frontend = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8080/' -TimeoutSec 5
        if ($frontend.Content -notmatch 'IncidentPilot demo') { throw 'Unexpected frontend page' }
        $health = Invoke-RestMethod -Uri 'http://localhost:8080/healthz' -TimeoutSec 5
        if ($health.status -ne 'healthy' -or $health.database -ne 'reachable') { throw 'Database health check failed' }
        $message = Invoke-RestMethod -Uri 'http://localhost:8080/api/message' -TimeoutSec 5
        if ($message.source -ne 'postgres' -or $message.message -ne 'Hello from PostgreSQL. The demo application is healthy.') {
            throw 'Unexpected database-backed message'
        }
        $metrics = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8080/metrics' -TimeoutSec 5
        if ($metrics.Content -notmatch 'demo_api_requests_total\{outcome="success"\}') { throw 'API metric missing' }
        $query = [Uri]::EscapeDataString('demo_api_requests_total{job="demo-api",outcome="success"}')
        $result = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/query?query=$query" -TimeoutSec 5
        if ($result.status -ne 'success' -or $result.data.result.Count -ne 1 -or [double]$result.data.result[0].value[1] -lt 1) {
            throw 'Prometheus has not scraped a successful database request yet'
        }
        $upQuery = [Uri]::EscapeDataString('up{job="demo-api"}')
        $up = Invoke-RestMethod -Uri "http://localhost:9090/api/v1/query?query=$upQuery" -TimeoutSec 5
        if ($up.data.result.Count -ne 1 -or [double]$up.data.result[0].value[1] -ne 1) { throw 'Prometheus scrape target is down' }
        Write-Host 'M0 smoke check passed: frontend -> API -> PostgreSQL; Prometheus target up and counter scraped.'
        return
    } catch {
        $lastFailure = $_.Exception.Message
        Start-Sleep -Seconds 2
    }
} while ((Get-Date) -lt $deadline)
throw "M0 smoke check failed: $lastFailure"
