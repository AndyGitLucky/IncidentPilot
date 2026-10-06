# M5: repeated fixtures, comparison metrics, and local OpenTelemetry traces.
param(
    [ValidateRange(1, 20)][int]$Repetitions = 2,
    [switch]$FixturesOnly,
    [switch]$HumanRepair,
    [switch]$Observability,
    [string]$Output
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$arguments = @('run', '--frozen', '--extra', 'agent', 'python', '-m', 'eval.benchmark', '--repetitions', "$Repetitions")
if ($FixturesOnly) { $arguments += '--fixtures-only' }
if ($HumanRepair) { $arguments += '--human-repair' }
if ($Output) { $arguments += @('--output', $Output) }
if ($Observability) {
    $arguments += @('--otlp-endpoint', 'http://127.0.0.1:14318/v1/traces', '--pushgateway', '127.0.0.1:19091')
}
Push-Location $repoRoot
try {
    & uv @arguments
    if ($LASTEXITCODE -ne 0) { throw 'M5 benchmark failed; inspect the output directory results.json and traces.jsonl.' }
} finally {
    Pop-Location
}
