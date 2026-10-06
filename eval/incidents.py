"""Evaluate all five fixed incidents; live agent writes still require human input."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
import time
from uuid import uuid4

from kubernetes import client, config
from langgraph.types import Command

from agent.diagnosis import OpenRouterDiagnoser, validate_diagnosis
from agent.fixtures import ACTIONS
from agent.graph import build_graph, collect_evidence
from agent.kubernetes import CONTEXT, NAMESPACE, ROOT, TIMEOUT, KubernetesReader
from agent.mcp_client import connect_tools
from agent.models import Diagnosis
from agent.repair import RestrictedRepairRunner
from incidents.fixture import mutate, observe


def resource_signature(reader):
    """Ignore controller status changes and API pod churn, but retain workload specs."""
    resources = []
    for item in reader.items(reader.apps.list_namespaced_deployment(NAMESPACE, limit=100, _request_timeout=TIMEOUT)):
        resources.append(("Deployment", item.metadata.name, item.metadata.uid, item.metadata.generation,
                          reader.apps.api_client.sanitize_for_serialization(item.spec)))
    for item in reader.items(reader.core.list_namespaced_service(NAMESPACE, limit=100, _request_timeout=TIMEOUT)):
        resources.append(("Service", item.metadata.name, item.metadata.uid, None,
                          reader.core.api_client.sanitize_for_serialization(item.spec)))
    for item in reader.items(reader.core.list_namespaced_persistent_volume_claim(NAMESPACE, limit=100, _request_timeout=TIMEOUT)):
        resources.append(("PVC", item.metadata.name, item.metadata.uid, None,
                          reader.core.api_client.sanitize_for_serialization(item.spec)))
    for item in reader.items(reader.core.list_namespaced_pod(NAMESPACE, limit=100, _request_timeout=TIMEOUT)):
        if (item.metadata.labels or {}).get("app") != "api":
            resources.append(("Pod", item.metadata.name, item.metadata.uid, None,
                              reader.core.api_client.sanitize_for_serialization(item.spec)))
    return sorted(resources, key=lambda r: (r[0], r[1]))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def unrelated_resources(signature, incident):
    target_kind = "Service" if incident == "bad_selector" else "Deployment"
    return [r for r in signature if not (r[0] == target_kind and r[1] == "api")]


async def evaluate(args):
    diagnoser = None if args.fixtures_only else OpenRouterDiagnoser.from_env()
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"milestone": "M4", "mode": "fixtures_only" if args.fixtures_only else "live_model",
               "human_repair": args.human_repair, "started_at": datetime.now(timezone.utc).isoformat(),
               "cases": [], "passed": False,
               "limitations": ["Single fixture pass; statistical benchmark and observability are M5.",
                               "Manual restoration is not counted as agent repair success."]}
    configuration = client.Configuration()
    config.load_kube_config(config_file=str(ROOT / ".local" / "kubeconfig"), context=CONTEXT,
                           client_configuration=configuration, persist_config=False)
    with client.ApiClient(configuration) as api:
        admin = KubernetesReader(api)
        verifier = RestrictedRepairRunner(api)
        current = admin.get_deployment(NAMESPACE, "api")["deployment"]
        verifier.verify({"deployment_uid": current["uid"], "generation": current["generation"]})
        async with connect_tools() as tools:
            for incident in ACTIONS:
                started = time.monotonic()
                case = {"incident_id": incident, "injected": False, "repeat_injection": False,
                        "diagnosis_correct": None, "proposal_correct": None, "denial_no_write": None,
                        "agent_repair_success": None, "restored": False, "repeat_restore": False,
                        "unrelated_resources_unchanged": False, "passed": False}
                summary["cases"].append(case)
                baseline = resource_signature(admin)
                unrelated = unrelated_resources(baseline, incident)
                try:
                    mutate(admin, incident, True)
                    await observe(admin, incident, args.timeout)
                    case["injected"] = True
                    before = resource_signature(admin)
                    mutate(admin, incident, True)
                    case["repeat_injection"] = before == resource_signature(admin)
                    if args.fixtures_only:
                        evidence = await collect_evidence(tools)
                        # This validates fixture symptoms only. It is not an LLM accuracy score.
                        candidate = Diagnosis(status="diagnosed", incident_type=incident,
                            suspected_root_cause="Manual fixed-fixture evidence check", confidence=1,
                            evidence_ids=[e.id for e in evidence if not e.error], alternative_hypotheses=[])
                        observed = validate_diagnosis(candidate, evidence)
                        write_json(args.output / f"{incident}-fixture.json", {
                            "mode": "fixtures_only", "evidence": [e.model_dump(mode="json") for e in evidence],
                            "fixture_symptoms_validated": observed.status == "diagnosed"})
                        if observed.status != "diagnosed":
                            raise RuntimeError("Read-only MCP evidence did not validate the fixture symptoms")
                    else:
                        graph = build_graph(tools, diagnoser, repair=True)
                        graph_config = {"configurable": {"thread_id": str(uuid4())}}
                        state = await graph.ainvoke({}, graph_config)
                        report = state["report"]
                        write_json(args.output / f"{incident}-pending.json", report.model_dump(mode="json"))
                        case["diagnosis_correct"] = (report.diagnosis.status == "diagnosed"
                                                     and report.diagnosis.incident_type == incident)
                        case["proposal_correct"] = bool(report.proposal and report.proposal.action == ACTIONS[incident])
                        if not state.get("__interrupt__") or not case["diagnosis_correct"] or not case["proposal_correct"]:
                            raise RuntimeError("Expected supported diagnosis and fixed repair proposal")
                        state = await graph.ainvoke(Command(resume="deny"), graph_config)
                        denied = state["report"]
                        write_json(args.output / f"{incident}-denied.json", denied.model_dump(mode="json"))
                        case["denial_no_write"] = (before == resource_signature(admin)
                                                   and denied.outcome == "denied"
                                                   and "EXECUTE" not in denied.stages)
                        case["tokens"] = report.usage
                        if not case["denial_no_write"]:
                            raise RuntimeError("Resources changed without approval")
                        if args.human_repair:
                            # Fresh diagnosis and proposal after denial. Never synthesize approval.
                            graph = build_graph(tools, diagnoser, repair=True)
                            graph_config = {"configurable": {"thread_id": str(uuid4())}}
                            state = await graph.ainvoke({}, graph_config)
                            pending = state["report"]
                            write_json(args.output / f"{incident}-human-pending.json", pending.model_dump(mode="json"))
                            if not state.get("__interrupt__"):
                                raise RuntimeError("No new proposal available for human approval")
                            print(pending.proposal.model_dump_json(indent=2), flush=True)
                            try:
                                answer = input(f"Type approve {pending.proposal.id} to execute, or deny: ").strip()
                            except (EOFError, KeyboardInterrupt):
                                answer = "deny"
                            state = await graph.ainvoke(Command(resume=answer), graph_config)
                            write_json(args.output / f"{incident}-human-result.json", state["report"].model_dump(mode="json"))
                            case["agent_repair_success"] = state["report"].outcome == "recovered"
                            if not case["agent_repair_success"]:
                                raise RuntimeError("Human-approved agent recovery was not confirmed")
                except Exception as error:
                    # Sanitized graph/provider failures are retained in their per-case reports.
                    case["error"] = type(error).__name__ + ": evaluation step failed; inspect the per-case reports"
                finally:
                    try:
                        execution = mutate(admin, incident, False)
                        verifier.verify(execution, timeout_seconds=args.timeout)
                        case["restored"] = True
                        restored = resource_signature(admin)
                        execution = mutate(admin, incident, False)
                        verifier.verify(execution, timeout_seconds=args.timeout)
                        case["repeat_restore"] = restored == resource_signature(admin)
                        after = resource_signature(admin)
                        case["unrelated_resources_unchanged"] = unrelated == unrelated_resources(after, incident)
                        shell = shutil.which("pwsh") or shutil.which("powershell")
                        if not shell:
                            raise RuntimeError("PowerShell is required for the demo smoke check")
                        subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts" / "verify.ps1")], check=True)
                    except Exception:
                        case["cleanup_error"] = "Manual restoration or full health check failed; inspect the demo before continuing"
                    case["duration_seconds"] = round(time.monotonic() - started, 3)
                    case["passed"] = (not case.get("error") and not case.get("cleanup_error")
                                      and all(case[k] for k in ("injected", "repeat_injection", "restored",
                                                               "repeat_restore", "unrelated_resources_unchanged")))
                    write_json(args.output / "results.json", summary)
                    print(f"M4 {incident}: {'passed' if case['passed'] else 'failed'}", flush=True)
                if not case["restored"] or case.get("cleanup_error"):
                    break
    summary["passed"] = len(summary["cases"]) == len(ACTIONS) and all(c["passed"] for c in summary["cases"])
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    if not args.fixtures_only:
        summary["diagnosis_accuracy"] = sum(c["diagnosis_correct"] is True for c in summary["cases"]) / len(ACTIONS)
    write_json(args.output / "results.json", summary)
    return 0 if summary["passed"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-only", action="store_true", help="Validate local symptoms and recovery without OpenRouter; no model accuracy score")
    parser.add_argument("--human-repair", action="store_true", help="Request explicit human approval for each live repair")
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "m4")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.fixtures_only and args.human_repair:
        parser.error("human repair requires live diagnosis")
    if not 1 <= args.timeout <= 300:
        parser.error("timeout must be between 1 and 300 seconds")
    raise SystemExit(asyncio.run(evaluate(args)))


if __name__ == "__main__":
    main()
