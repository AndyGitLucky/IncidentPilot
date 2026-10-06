"""Exercise the actual M3 PowerShell snapshot filter without running live repairs."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [shell for name in ("powershell", "pwsh") if (shell := shutil.which(name))]


def resource(kind, name, **spec):
    return {"kind": kind, "name": name, "uid": f"{kind}-{name}", "generation": 1, "spec": spec}


def snapshots():
    before = [
        resource("Deployment", "api", image="broken"),
        resource("Deployment", "frontend", replicas=1),
        resource("PersistentVolumeClaim", "postgres-data", accessModes=["ReadWriteOnce"]),
        resource("Pod", "api-broken", containers=[{"name": "api", "image": "broken"}]),
        resource("Pod", "api-healthy", containers=[{"name": "api", "image": "healthy"}]),
        resource("Pod", "frontend-pod", containers=[{"name": "frontend", "image": "nginx"}]),
        resource("Service", "api", selector={"app": "api"}),
        resource("Service", "frontend", selector={"app": "frontend"}),
    ]
    after = deepcopy(before)
    after[0]["generation"] = 2
    after[0]["spec"]["image"] = "healthy"
    after = [r for r in after if r["name"] != "api-broken"]
    return before, after


@pytest.mark.skipif(not SHELLS, reason="PowerShell is required for the Windows acceptance regression")
@pytest.mark.parametrize("shell", SHELLS)
def test_acceptance_filter_allows_api_recovery_and_detects_unrelated_changes(shell, tmp_path):
    before, after = snapshots()
    cases = [{"label": "expected_repair", "before": before, "after": after, "equal": True}]
    for kind, name in [("Deployment", "frontend"), ("PersistentVolumeClaim", "postgres-data"),
                       ("Pod", "frontend-pod"), ("Service", "api"), ("Service", "frontend")]:
        for field in ("uid", "spec", "removed"):
            altered = deepcopy(after)
            item = next(r for r in altered if r["kind"] == kind and r["name"] == name)
            if field == "removed":
                altered.remove(item)
            else:
                item[field] = "changed" if field == "uid" else {**item[field], "changed": True}
            cases.append({"label": f"{kind}/{name}:{field}", "before": before, "after": altered, "equal": False})
    added = deepcopy(after) + [resource("Service", "unexpected", selector={"app": "api"})]
    cases.append({"label": "unexpected_resource", "before": before, "after": added, "equal": False})
    for unchanged in ([], [resource("Service", "api", selector={"app": "api"})]):
        cases.append({"label": "small_snapshot", "before": unchanged, "after": unchanged, "equal": True})
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    # Load only the function's AST body, so this test cannot invoke the acceptance
    # script's setup, OpenRouter, injection, approval, runner, or cleanup commands.
    script = tmp_path / "check.ps1"
    script.write_text("""param([string]$Source, [string]$Cases)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Acceptance script parse failed' }
$function = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-UnrelatedDemoSignature'}, $true)
if (-not $function) { throw 'Snapshot comparison function missing' }
. ([scriptblock]::Create($function.Extent.Text))
$casesData = Get-Content -Raw -Encoding UTF8 -LiteralPath $Cases | ConvertFrom-Json
foreach ($case in $casesData) {
    $before = ConvertTo-Json -InputObject @($case.before) -Depth 40 -Compress
    $after = ConvertTo-Json -InputObject @($case.after) -Depth 40 -Compress
    $equal = (Get-UnrelatedDemoSignature $before) -eq (Get-UnrelatedDemoSignature $after)
    if ($equal -ne $case.equal) { throw "Incorrect unrelated-resource result: $($case.label)" }
}
Write-Output 'Snapshot comparison regression checks passed'
""", encoding="utf-8")
    result = subprocess.run([shell, "-NoProfile", "-File", str(script),
                             str(ROOT / "scripts" / "verify-repair.ps1"), str(cases_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
