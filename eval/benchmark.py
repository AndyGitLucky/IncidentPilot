"""Repeated five-fixture benchmark with A/B/C comparisons and local telemetry."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from kubernetes import client, config
from langgraph.types import Command

from agent.diagnosis import OpenRouterDiagnoser, validate_diagnosis
from agent.fixtures import ACTIONS
from agent.graph import build_graph, collect_evidence
from agent.kubernetes import CONTEXT, NAMESPACE, ROOT, KubernetesReader
from agent.mcp_client import connect_tools
from agent.models import Diagnosis
from agent.repair import RestrictedRepairRunner
from eval.comparison import ComparisonDiagnoser, MODES
from eval.incidents import resource_signature, unrelated_resources, write_json
from eval.scoring import aggregate, markdown_table, score
from incidents.fixture import mutate, observe
from telemetry.runtime import Telemetry, span


class FrozenTools:
    """Record one read-only snapshot and replay it identically to all comparisons."""
    cached = True

    def __init__(self, tools):
        self.tools = tools
        self.responses = {}
        self.calls = 0
        self.recording = True

    async def call(self, name, arguments):
        key = (name, json.dumps(arguments, sort_keys=True))
        if self.recording:
            self.calls += 1
            try:
                self.responses[key] = (True, deepcopy(await self.tools.call(name, arguments)))
            except Exception:
                self.responses[key] = (False, None)
        success, data = self.responses[key]
        if not success:
            raise RuntimeError("Read tool failed in the shared snapshot")
        return deepcopy(data)


def empty_row(mode, incident):
    return {"mode": mode, "incident_id": incident, "candidate_correct": False,
            "diagnosis_correct": False, "top2_correct": False, "unsupported_claim": False,
            "evidence_completeness": 0.0, "unnecessary_proposal": False,
            "proposal_correct": False, "unsafe_write": None, "repair_attempted": False,
            "agent_repair_success": None, "regression": None, "tokens": None,
            "tool_calls": 0, "duration_seconds": None, "passed": False}


def smoke_check():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("PowerShell is required for the full demo smoke check")
    subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts" / "verify.ps1")], check=True)


def save(args, summary, telemetry):
    summary["metrics"] = aggregate(summary)
    summary["telemetry_export_errors"] = telemetry.export_errors
    write_json(args.output / "results.json", summary)
    (args.output / "metrics.md").write_text(markdown_table(summary["metrics"]), encoding="utf-8")
    telemetry.publish()


async def compare(snapshot, provider, mode, incident, path, detected_at, admin, telemetry):
    row = empty_row(mode, incident)
    started = time.monotonic()
    before = resource_signature(admin)
    diagnoser = ComparisonDiagnoser(provider, mode)
    graph = build_graph(snapshot, diagnoser, repair=True)
    configuration = {"configurable": {"thread_id": str(uuid4())}}
    try:
        with span("comparison", mode=mode):
            state = await graph.ainvoke({}, configuration)
            pending = state["report"]
            write_json(path / f"{mode}-pending.json", pending.model_dump(mode="json"))
            row.update(score(pending, incident))
            row["tool_calls"] = len(pending.evidence)  # logical, replayed reads; physical calls are shared
            row["runbooks"] = [book["name"] for book in diagnoser.retrieved]
            if pending.diagnosis.status == "diagnosed":
                row["time_to_diagnosis_seconds"] = time.monotonic() - detected_at
            if state.get("__interrupt__"):
                state = await graph.ainvoke(Command(resume="deny"), configuration)
            final = state["report"]
            write_json(path / f"{mode}-result.json", final.model_dump(mode="json"))
            row["unsafe_write"] = (before != resource_signature(admin) or "EXECUTE" in final.stages)
            row["denied"] = final.outcome == "denied"
            # A supported or rejected model answer is a completed measurement.
            # Accuracy failures are measured, not confused with harness failure.
            row["passed"] = (not row["unsafe_write"] and pending.model_diagnosis is not None
                             and final.outcome in {"denied", "not_needed", "blocked"})
            if row["diagnosis_correct"]:
                telemetry.correct.labels(mode).inc()
            if row["unsafe_write"]:
                telemetry.unsafe.inc()
            for kind in ("prompt_tokens", "completion_tokens"):
                if kind in final.usage:
                    telemetry.tokens.labels(mode, kind).inc(final.usage[kind])
    except Exception:
        row["error"] = "Comparison failed; inspect local reports and traces."
        row["unsafe_write"] = before != resource_signature(admin)
        if row["unsafe_write"]:
            telemetry.unsafe.inc()
    finally:
        row["duration_seconds"] = time.monotonic() - started
    return row


async def human_repair(tools, provider, incident, path, started, row, telemetry, verifier):
    """A fresh live proposal, after all read-only comparisons. Never reuse a frozen proposal."""
    with span("human_repair", mode="structured"):
        graph = build_graph(tools, ComparisonDiagnoser(provider, "structured"), repair=True)
        configuration = {"configurable": {"thread_id": str(uuid4())}}
        state = await graph.ainvoke({}, configuration)
        pending = state["report"]
        write_json(path / "human-pending.json", pending.model_dump(mode="json"))
        row["human_repair_usage"] = pending.usage
        row["human_repair_tool_calls"] = len(pending.evidence)
        for kind in ("prompt_tokens", "completion_tokens"):
            if kind in pending.usage:
                telemetry.tokens.labels("structured_human_repair", kind).inc(pending.usage[kind])
        if not state.get("__interrupt__") or pending.diagnosis.incident_type != incident:
            row["human_repair_error"] = "No supported fresh proposal; no approval requested."
            return
        print(pending.proposal.model_dump_json(indent=2), flush=True)
        with span("human_approval"):
            try:
                answer = input(f"Type approve {pending.proposal.id} to execute, or deny: ").strip()
            except (EOFError, KeyboardInterrupt):
                answer = "deny"
        row["repair_attempted"] = answer == f"approve {pending.proposal.id}"
        state = await graph.ainvoke(Command(resume=answer), configuration)
        result = state["report"]
        write_json(path / "human-result.json", result.model_dump(mode="json"))
        if not row["repair_attempted"]:
            return
        row["agent_repair_success"] = result.outcome == "recovered"
        if row["agent_repair_success"]:
            row["agent_recovery_seconds"] = time.monotonic() - started
            telemetry.mttr.set(row["agent_recovery_seconds"])
            telemetry.repairs.labels("structured").inc()
            try:
                verifier.verify(result.execution)
                smoke_check()
                row["regression"] = False
            except Exception:
                row["regression"] = True
                row["passed"] = False
        else:
            row["passed"] = False


async def evaluate(args):
    args.output.mkdir(parents=True, exist_ok=True)
    # Output is a run directory, never silently overwrite benchmark evidence.
    if (args.output / "results.json").exists():
        raise RuntimeError("Output already contains a benchmark; choose a new --output directory")
    modes = ("fixtures",) if args.fixtures_only else MODES
    summary = {"schema_version": 1, "milestone": "M5", "mode": "fixtures_only" if args.fixtures_only else "live_model",
               "repetitions": args.repetitions, "modes": list(modes), "human_repair": args.human_repair,
               "planned_incident_attempts_per_mode": args.repetitions * len(ACTIONS),
               "started_at": datetime.now(timezone.utc).isoformat(), "episodes": [], "passed": False,
               "limitations": ["Small local fixture sample; accuracy is incident-type label accuracy, not semantic grading.",
                   "Top-2 counts the primary type and the first explicit alternative incident key.",
                   "Evidence completeness is required cited evidence-block coverage, not a substitute for support validation.",
                   "Physical MCP collection is shared; comparison tool counts are replayed logical reads.",
                   "A/B/C compare prompt and evidence representation plus retrieval, not dynamic tool selection.",
                   "MTTD starts at injection; MTTDg starts at symptom detection and includes comparison-order waiting.",
                   "Denied/manual repairs do not populate agent repair success, MTTR, or regression.",
                   "Regression is an immediate independent post-repair health check, not long-term monitoring.",
                   "Human repair uses a fresh structured diagnosis after A/B/C; its usage is separate.",
                   "Provider failures with no usage are missing token data, not zero tokens."]}
    telemetry = Telemetry(args.output, otlp_endpoint=args.otlp_endpoint, pushgateway=args.pushgateway)
    try:
        with telemetry.activate():
            provider = None if args.fixtures_only else OpenRouterDiagnoser.from_env()
            summary["model"] = provider.model if provider else None
            configuration = client.Configuration()
            config.load_kube_config(config_file=str(ROOT / ".local" / "kubeconfig"), context=CONTEXT,
                                   client_configuration=configuration, persist_config=False)
            with client.ApiClient(configuration) as api:
                admin = KubernetesReader(api)
                verifier = RestrictedRepairRunner(api)
                current = admin.get_deployment(NAMESPACE, "api")["deployment"]
                verifier.verify({"deployment_uid": current["uid"], "generation": current["generation"]})
                smoke_check()
                async with connect_tools() as tools:
                    schedule = [(0, "none")] + [(repeat, incident) for repeat in range(1, args.repetitions + 1) for incident in ACTIONS]
                    for repeat, incident in schedule:
                        started = time.monotonic()
                        episode = {"repetition": repeat, "incident_id": incident, "comparisons": [], "restored": False}
                        summary["episodes"].append(episode)
                        path = args.output / f"{repeat:02d}-{incident}"
                        path.mkdir()
                        baseline = resource_signature(admin)
                        unrelated = unrelated_resources(baseline, incident)
                        with span("incident", incident_type=incident, repetition=repeat) as root:
                            episode["trace_id"] = format(root.get_span_context().trace_id, "032x")
                            telemetry.traces.clear()
                            telemetry.traces.labels(episode["trace_id"]).set(1)
                            telemetry.active.labels(incident).set(incident != "none")
                            injected_at = started
                            try:
                                if incident != "none":
                                    telemetry.incidents.inc()
                                    with span("injection"):
                                        injected_at = time.monotonic()
                                        mutate(admin, incident, True)
                                    with span("alert"):
                                        await observe(admin, incident, args.timeout)
                                    episode["detection_seconds"] = time.monotonic() - injected_at
                                detected_at = time.monotonic()
                                before_collection = resource_signature(admin)
                                snapshot = FrozenTools(tools)
                                snapshot.cached = False
                                with span("shared_collection"):
                                    evidence = await collect_evidence(snapshot)
                                snapshot.recording = False
                                snapshot.cached = True
                                episode["shared_tool_calls"] = snapshot.calls
                                telemetry.pods.clear()
                                for e in evidence:
                                    for pod in e.data.get("pods", []):
                                        if pod.get("labels", {}).get("app") == "api" and not pod["terminating"]:
                                            status = "ready" if all(c["ready"] for c in pod["containers"]) else "not_ready"
                                            telemetry.pods.labels(status).inc()
                                if args.fixtures_only:
                                    # Validate observations with ground truth locally; never call this model accuracy.
                                    diagnosis = Diagnosis(status="no_active_incident" if incident == "none" else "diagnosed",
                                        incident_type=incident, suspected_root_cause="Fixture symptom validation only", confidence=1,
                                        evidence_ids=[e.id for e in evidence if not e.error], alternative_hypotheses=[])
                                    validated = validate_diagnosis(diagnosis, evidence)
                                    row = empty_row("fixtures", incident)
                                    row.update({key: None for key in ("candidate_correct", "diagnosis_correct", "top2_correct",
                                                "unsupported_claim", "evidence_completeness", "unnecessary_proposal")})
                                    row["read_only_no_write"] = before_collection == resource_signature(admin)
                                    row.update(passed=validated.incident_type == incident and row["read_only_no_write"])
                                    episode["comparisons"].append(row)
                                    write_json(path / "fixture.json", {"evidence": [e.model_dump(mode="json") for e in evidence],
                                                                      "symptoms_validated": row["passed"]})
                                else:
                                    order = list(MODES)
                                    offset = repeat % len(order)
                                    order = order[offset:] + order[:offset]
                                    for mode in order:
                                        row = await compare(snapshot, provider, mode, incident, path, detected_at, admin, telemetry)
                                        episode["comparisons"].append(row)
                                        save(args, summary, telemetry)
                                        print(f"M5 {repeat}/{incident}/{mode}: {'passed' if row['passed'] else 'failed'}", flush=True)
                                        if row["unsafe_write"]:
                                            raise RuntimeError("Resources changed before approval; stopping benchmark")
                                    if args.human_repair and incident != "none":
                                        row = next(r for r in episode["comparisons"] if r["mode"] == "structured")
                                        await human_repair(tools, provider, incident, path, injected_at, row, telemetry, verifier)
                            except Exception:
                                episode["error"] = "Episode failed; inspect local reports and traces."
                                attempted = {r["mode"] for r in episode["comparisons"]}
                                for mode in modes:
                                    if mode not in attempted:
                                        row = empty_row(mode, incident)
                                        if args.fixtures_only:
                                            for key in ("candidate_correct", "diagnosis_correct", "top2_correct", "unsupported_claim",
                                                        "evidence_completeness", "unnecessary_proposal"):
                                                row[key] = None
                                        episode["comparisons"].append(row)
                            finally:
                                with span("manual_cleanup"):
                                    recovery_started = time.monotonic()
                                    try:
                                        execution = (mutate(admin, incident, False) if incident != "none" else
                                                     {"deployment_uid": current["uid"], "generation": current["generation"]})
                                        verifier.verify(execution, timeout_seconds=args.timeout)
                                        smoke_check()
                                        episode["restored"] = True
                                        episode["unrelated_resources_unchanged"] = unrelated == unrelated_resources(resource_signature(admin), incident)
                                        if not episode["unrelated_resources_unchanged"]:
                                            raise RuntimeError("Unrelated resources changed")
                                        if incident != "none":
                                            episode["manual_recovery_seconds"] = time.monotonic() - recovery_started
                                            telemetry.manual.set(episode["manual_recovery_seconds"])
                                        telemetry.health.set(1)
                                    except Exception:
                                        episode["cleanup_error"] = "Restoration or resource preservation failed; inspect demo before continuing."
                                        telemetry.health.set(0)
                                telemetry.active.labels(incident).set(0 if episode["restored"] else 1)
                                telemetry.stage.clear()
                                episode["duration_seconds"] = time.monotonic() - started
                                episode["passed"] = (not episode.get("error") and not episode.get("cleanup_error") and
                                                      all(r["passed"] for r in episode["comparisons"]))
                                save(args, summary, telemetry)
                                print(f"M5 episode {repeat}/{incident}: {'passed' if episode['passed'] else 'failed'}", flush=True)
                        if episode.get("cleanup_error") or any(r.get("unsafe_write") for r in episode["comparisons"]):
                            break
            summary["passed"] = (len(summary["episodes"]) == 1 + args.repetitions * len(ACTIONS) and
                                  all(e["passed"] for e in summary["episodes"]))
    except Exception:
        summary["error"] = "Benchmark could not complete; check dependencies, credentials, provider, and demo health."
    finally:
        summary["completed_at"] = datetime.now(timezone.utc).isoformat()
        telemetry.close()
        summary["metrics"] = aggregate(summary)
        summary["telemetry_export_errors"] = telemetry.export_errors
        write_json(args.output / "results.json", summary)
        (args.output / "metrics.md").write_text(markdown_table(summary["metrics"]), encoding="utf-8")
        print(f"M5 artifacts: {args.output}", flush=True)
    if telemetry.export_errors:
        summary["passed"] = False
        write_json(args.output / "results.json", summary)
    return 0 if summary["passed"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--fixtures-only", action="store_true", help="No provider requests or model scores")
    parser.add_argument("--human-repair", action="store_true", help="Prompt for one fresh structured repair per episode")
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "m5" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--otlp-endpoint", help="Local Tempo OTLP HTTP /v1/traces endpoint")
    parser.add_argument("--pushgateway", help="Local Pushgateway host:port")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 20 or not 1 <= args.timeout <= 300:
        parser.error("repetitions must be 1..20 and timeout 1..300 seconds")
    if args.fixtures_only and args.human_repair:
        parser.error("human repair requires live model diagnosis")
    # Keep telemetry local; no credentials or arbitrary external destinations.
    from urllib.parse import urlparse
    for value in (args.otlp_endpoint, args.pushgateway):
        if value and urlparse(value if "://" in value else "http://" + value).hostname not in {"localhost", "127.0.0.1"}:
            parser.error("telemetry endpoints must use localhost or 127.0.0.1")
    raise SystemExit(asyncio.run(evaluate(args)))


if __name__ == "__main__":
    main()
