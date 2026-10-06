# Manual M4 fixture inject; uses only checkout-local demo credentials.
param([ValidateRange(1, 300)][int]$TimeoutSeconds = 120)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repoRoot
try {
    & uv run --frozen --extra agent python -m incidents.fixture bad_selector inject --timeout $TimeoutSeconds
    if ($LASTEXITCODE -ne 0) { throw 'bad_selector inject failed; inspect demo state.' }
} finally {
    Pop-Location
}
