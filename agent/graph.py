"""M2 graph: COLLECT -> HYPOTHESIZE -> END. All cluster calls pass through MCP."""

from datetime import datetime, timezone
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.diagnosis import DiagnosisError, inconclusive, validate_diagnosis
from agent.models import DiagnosisReport, Evidence


class AgentState(TypedDict, total=False):
    evidence: list[Evidence]
    stages: list[str]
    report: DiagnosisReport


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


def build_graph(tools, diagnoser):
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
    graph.add_edge("HYPOTHESIZE", END)
    return graph.compile()
