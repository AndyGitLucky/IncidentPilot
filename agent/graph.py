"""Read-only diagnosis, optionally followed by a human-gated fixed repair."""

from datetime import datetime, timezone
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from agent.diagnosis import DiagnosisError, inconclusive, validate_diagnosis
from agent.models import DiagnosisReport, Evidence, RepairReport
from agent.repair import RepairError, RestrictedRepairRunner, check_approval, propose_repair


class AgentState(TypedDict, total=False):
    evidence: list[Evidence]
    stages: list[str]
    report: DiagnosisReport
    approval: str


async def collect_evidence(tools):
    evidence = []

    async def read(name, arguments, source="kubernetes"):
        try:
            data = await tools.call(name, arguments)
            error = False
        except Exception:
            data = {"error": f"{name} unavailable; check demo resources and read-only credentials"}
            error = True
        item = Evidence(
            id=f"e{len(evidence) + 1}", source=source,
            query=f"{name}({json.dumps(arguments, sort_keys=True)})",
            observation=json.dumps(data, sort_keys=True, ensure_ascii=False),
            timestamp=data.get("collected_at") or datetime.now(timezone.utc),
            data=data, error=error,
        )
        evidence.append(item)
        return data

    deployment = await read("get_deployment", {"namespace": "incidentpilot", "name": "api"})
    snapshot = await read("get_pods", {"namespace": "incidentpilot"})
    events = await read("get_events", {"namespace": "incidentpilot"}, "events")
    rs_uids = {rs["uid"] for rs in deployment.get("deployment", {}).get("replica_sets", [])}
    active_pods = [pod for pod in snapshot.get("pods", []) if not pod["terminating"] and any(
        o["kind"] == "ReplicaSet" and o["uid"] in rs_uids and o["controller"] for o in pod["owners"]
    )]
    # Historical pod events remain after recovery. Keep only current target UIDs.
    if "events" in events:
        target_uids = {pod["uid"] for pod in active_pods}
        target_uids.add(deployment.get("deployment", {}).get("uid"))
        total = len(events["events"])
        events["events"] = [event for event in events["events"] if event["pod_uid"] in target_uids]
        events["scope"] = "current API deployment and nonterminating owned pod UIDs"
        events["excluded_other_resource_events"] = total - len(events["events"])
        evidence[-1].data = events
        evidence[-1].observation = json.dumps(events, sort_keys=True, ensure_ascii=False)
    for pod in active_pods:
        await read("describe_pod", {"namespace": "incidentpilot", "pod": pod["name"]})
    await read("search_logs", {"service": "api", "since_minutes": 10}, "logs")
    return evidence


def build_graph(tools, diagnoser, *, repair=False, runner_factory=RestrictedRepairRunner):
    async def collect(state):
        return {"evidence": await collect_evidence(tools), "stages": ["COLLECT"]}

    async def hypothesize(state):
        evidence = state["evidence"]
        candidate = None
        try:
            result = await diagnoser.diagnose(evidence)
            candidate = result.diagnosis
            diagnosis = validate_diagnosis(result.diagnosis, evidence)
            model, usage = result.model, result.usage
        except DiagnosisError as error:
            diagnosis = inconclusive(evidence, str(error))
            model, usage = diagnoser.model, {}
        except Exception:
            diagnosis = inconclusive(evidence, "LLM diagnosis failed or returned invalid output; no supported conclusion is available.")
            model, usage = diagnoser.model, {}
        stages = [*state["stages"], "HYPOTHESIZE"]
        return {"stages": stages, "report": DiagnosisReport(
            stages=stages, evidence=evidence, diagnosis=diagnosis, model_diagnosis=candidate,
            model=model, usage=usage,
        )}

    graph = StateGraph(AgentState)
    graph.add_node("COLLECT", collect)
    graph.add_node("HYPOTHESIZE", hypothesize)
    graph.add_edge(START, "COLLECT")
    graph.add_edge("COLLECT", "HYPOTHESIZE")
    if not repair:
        graph.add_edge("HYPOTHESIZE", END)
        return graph.compile()

    def update(state, stage, **changes):
        report = state["report"]
        stages = [*state["stages"], stage]
        return {"stages": stages, "report": report.model_copy(update={"stages": stages, **changes})}

    def test(state):
        report = RepairReport(**state["report"].model_dump())
        report.diagnosis = validate_diagnosis(report.diagnosis, report.evidence)
        state = {**state, "report": report}
        return update(state, "TEST")

    def propose(state):
        proposal = propose_repair(state["report"])
        outcome = "pending" if proposal else (
            "not_needed" if state["report"].diagnosis.status == "no_active_incident" else "blocked")
        return update(state, "PROPOSE_FIX", proposal=proposal, outcome=outcome,
                      approval="pending" if proposal else "not_requested")

    def approve(state):
        proposal = state["report"].proposal
        answer = interrupt({"proposal": proposal.model_dump(mode="json"),
                            "instruction": f"Type approve {proposal.id} to execute; anything else denies."})
        if answer != f"approve {proposal.id}":
            return update(state, "WAIT_FOR_APPROVAL", approval="denied", outcome="denied")
        try:
            check_approval(proposal, answer)
        except RepairError as error:
            return update(state, "WAIT_FOR_APPROVAL", approval="denied", outcome="blocked", error=str(error))
        return {**update(state, "WAIT_FOR_APPROVAL", approval="approved"), "approval": answer}

    def execute(state):
        try:
            execution = runner_factory().rollback_image_tag(state["report"].proposal, state["approval"])
            return update(state, "EXECUTE", execution=execution)
        except RepairError as error:
            return update(state, "EXECUTE", outcome="failed", error=str(error))
        except Exception:
            return update(state, "EXECUTE", outcome="failed", error="Repair could not start; check separate repair credentials.")

    def verify(state):
        try:
            verification = runner_factory().verify(state["report"].execution)
            if verification.get("healthy") is not True:
                raise RepairError("Recovery verification did not confirm health.")
            return update(state, "VERIFY", outcome="recovered", verification=verification)
        except RepairError as error:
            return update(state, "VERIFY", outcome="failed", error=str(error), verification={"healthy": False})
        except Exception:
            return update(state, "VERIFY", outcome="failed", error="Recovery verification failed.", verification={"healthy": False})

    graph.add_node("TEST", test)
    graph.add_node("PROPOSE_FIX", propose)
    graph.add_node("WAIT_FOR_APPROVAL", approve)
    graph.add_node("EXECUTE", execute)
    graph.add_node("VERIFY", verify)
    graph.add_node("REPORT", lambda state: update(state, "REPORT"))
    graph.add_edge("HYPOTHESIZE", "TEST")
    graph.add_edge("TEST", "PROPOSE_FIX")
    graph.add_conditional_edges("PROPOSE_FIX", lambda state: (
        "WAIT_FOR_APPROVAL" if state["report"].proposal else "REPORT"))
    graph.add_conditional_edges("WAIT_FOR_APPROVAL", lambda state: (
        "EXECUTE" if state["report"].approval == "approved" else "REPORT"))
    graph.add_conditional_edges("EXECUTE", lambda state: (
        "REPORT" if state["report"].outcome == "failed" else "VERIFY"))
    graph.add_edge("VERIFY", "REPORT")
    graph.add_edge("REPORT", END)
    return graph.compile(checkpointer=InMemorySaver())
