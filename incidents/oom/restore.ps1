# Manual M4 fixture restore; uses only checkout-local demo credentials.
param([ValidateRange(1, 300)][int]$TimeoutSeconds = 120)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repoRoot
try {
    & uv run --frozen --extra agent python -m incidents.fixture oom restore --timeout $TimeoutSeconds
    if ($LASTEXITCODE -ne 0) { throw 'oom restore failed; inspect demo state.' }
    & (Join-Path $repoRoot 'scripts/verify.ps1')
} finally {
    Pop-Location
}
