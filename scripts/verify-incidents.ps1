# M4 evaluation. Default: live read-only LLM diagnosis, deny, manual restore.
param([switch]$FixturesOnly, [switch]$HumanRepair)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$arguments = @('run', '--frozen', '--extra', 'agent', 'python', '-m', 'eval.incidents')
if ($FixturesOnly) { $arguments += '--fixtures-only' }
if ($HumanRepair) { $arguments += '--human-repair' }
Push-Location $repoRoot
try {
    & uv @arguments
    if ($LASTEXITCODE -ne 0) { throw 'M4 evaluation failed; inspect .local/m4/results.json and per-case reports.' }
} finally {
    Pop-Location
}
