"""M4 evidence, approval, and patch-boundary checks across the fixed library."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("kubernetes")

from kubernetes import client
from langgraph.types import Command

from agent.diagnosis import validate_diagnosis
from agent.fixtures import ACTIONS, BAD_HOST, BAD_MEMORY, BAD_SELECTOR, CRASH_COMMAND, GOOD_MEMORY
from agent.graph import build_graph, collect_evidence
from agent.models import Diagnosis, ModelResult
from agent.repair import RepairError, RestrictedRepairRunner, check_approval, proposal_id, propose_repair
from incidents.fixture import fixed_patch
from test_diagnosis import FakeTools, GOOD_IMAGE
from test_repair import deployment


class IncidentTools(FakeTools):
    def __init__(self, incident):
        super().__init__(healthy=incident == "bad_selector")
        self.incident = incident
        dep = self.deployment["containers"][0]
        c = self.pods[0]["containers"][0]
        self.logs = []
        if incident != "bad_image":
            dep["image"] = c["image"] = GOOD_IMAGE
            self.pods[0]["phase"] = "Running"
            c["waiting_reason"] = "CrashLoopBackOff" if incident in {"crashloop", "oom"} else None
            self.events[0]["reason"] = "BackOff"
            self.events[0]["message"] = "Back-off restarting failed container api"
        if incident == "crashloop":
            dep["startup_profile"] = c["startup_profile"] = "crashloop_fixture"
            c.update(restart_count=3, last_terminated_reason="Error", last_exit_code=1)
            self.logs = ["timestamp IncidentPilot crashloop fixture: startup failed"]
        elif incident == "oom":
            dep["resources"] = deepcopy(BAD_MEMORY)
            c.update(resources=deepcopy(BAD_MEMORY), restart_count=2, last_terminated_reason="OOMKilled", last_exit_code=137)
        elif incident == "bad_env":
            dep["db_host"] = c["db_host"] = BAD_HOST
            self.logs = ["timestamp Database health check failed"]
        elif incident == "bad_selector":
            self.routing["service"]["selector"] = deepcopy(BAD_SELECTOR)
            self.routing["endpoints"] = []

    async def call(self, name, arguments):
        result = await super().call(name, arguments)
        if name == "search_logs":
            result["entries"] = [{"pod_uid": self.pods[0]["uid"], "container": "api",
                                  "status": "available", "lines": self.logs}]
        return result


class IncidentDiagnoser:
    model = "offline-test"

    def __init__(self, incident):
        self.incident = incident

    async def diagnose(self, evidence):
        return ModelResult(model=self.model, usage={}, diagnosis=Diagnosis(
            status="diagnosed", incident_type=self.incident, suspected_root_cause="Test hypothesis",
            confidence=0.95, evidence_ids=[e.id for e in evidence if not e.error], alternative_hypotheses=[]))


def report(incident):
    return asyncio.run(build_graph(IncidentTools(incident), IncidentDiagnoser(incident)).ainvoke({}))["report"]


@pytest.mark.parametrize("incident", ACTIONS)
@pytest.mark.parametrize("approval", ["deny", "approve wrong-id", "approved"])
def test_all_incidents_require_exact_approval_before_constructing_runner(incident, approval):
    factory = Mock()

    async def check():
        graph = build_graph(IncidentTools(incident), IncidentDiagnoser(incident), repair=True, runner_factory=factory)
        config = {"configurable": {"thread_id": incident}}
        pending = await graph.ainvoke({}, config)
        assert pending["__interrupt__"]
        assert pending["report"].diagnosis.incident_type == incident
        assert pending["report"].proposal.action == ACTIONS[incident]
        factory.assert_not_called()
        denied = await graph.ainvoke(Command(resume=approval), config)
        assert denied["report"].outcome == "denied"
        assert "EXECUTE" not in denied["report"].stages
        factory.assert_not_called()
    asyncio.run(check())


@pytest.mark.parametrize("incident", ACTIONS)
def test_all_approved_graphs_dispatch_only_the_selected_action_and_verify(incident):
    factory = Mock()
    action = getattr(factory.return_value, ACTIONS[incident])
    action.return_value = {"generation": 6}
    factory.return_value.verify.return_value = {"healthy": True}

    async def check():
        graph = build_graph(IncidentTools(incident), IncidentDiagnoser(incident), repair=True, runner_factory=factory)
        config = {"configurable": {"thread_id": incident}}
        pending = await graph.ainvoke({}, config)
        factory.assert_not_called()
        result = await graph.ainvoke(Command(resume=f"approve {pending['report'].proposal.id}"), config)
        assert result["report"].outcome == "recovered"
        action.assert_called_once()
        factory.return_value.verify.assert_called_once()
        await graph.ainvoke(Command(resume=f"approve {pending['report'].proposal.id}"), config)
        action.assert_called_once()
        assert [call[0] for call in factory.return_value.method_calls] == [ACTIONS[incident], "verify"]
    asyncio.run(check())


@pytest.mark.parametrize("actual,claimed", [(a, c) for a in ACTIONS for c in ACTIONS if a != c])
def test_incident_types_cannot_be_substituted_for_each_other(actual, claimed):
    evidence = asyncio.run(collect_evidence(IncidentTools(actual)))
    candidate = asyncio.run(IncidentDiagnoser(claimed).diagnose(evidence)).diagnosis
    assert validate_diagnosis(candidate, evidence).status == "inconclusive"


@pytest.mark.parametrize("incident", ["crashloop", "oom", "bad_env"])
@pytest.mark.parametrize("change", ["unrelated_pod", "terminating", "healthy", "wrong_configuration", "missing_citation"])
def test_new_failures_require_current_owned_pod_matching_configuration_and_citations(incident, change):
    tools = IncidentTools(incident)
    if change == "unrelated_pod":
        tools.pods[0]["owners"][0]["uid"] = "other-rs"
    elif change == "terminating":
        tools.pods[0]["terminating"] = True
    elif change == "healthy":
        tools.pods[0]["containers"][0]["ready"] = True
    elif change == "wrong_configuration":
        tools.pods[0]["containers"][0]["db_host"] = "other-host"
    evidence = asyncio.run(collect_evidence(tools))
    candidate = asyncio.run(IncidentDiagnoser(incident).diagnose(evidence)).diagnosis
    if change == "missing_citation":
        candidate.evidence_ids = ["e1"]
    assert validate_diagnosis(candidate, evidence).status == "inconclusive"


@pytest.mark.parametrize("incident", ["crashloop", "bad_env"])
@pytest.mark.parametrize("change", ["unrelated_logs", "wrong_container", "no_logs", "wrong_warning"])
def test_logs_and_crash_events_must_correlate_to_failed_container(incident, change):
    evidence = asyncio.run(collect_evidence(IncidentTools(incident)))
    logs = next(e for e in evidence if "entries" in e.data)
    if change == "unrelated_logs":
        logs.data["entries"][0]["pod_uid"] = "old-pod"
    elif change == "wrong_container":
        logs.data["entries"][0]["container"] = "other"
    elif change == "no_logs":
        logs.data["entries"][0]["lines"] = []
    else:
        next(e for e in evidence if "events" in e.data).data["events"] = []
    candidate = asyncio.run(IncidentDiagnoser(incident).diagnose(evidence)).diagnosis
    assert (validate_diagnosis(candidate, evidence).status == "diagnosed") == (change == "wrong_warning" and incident == "bad_env")


@pytest.mark.parametrize("change", ["termination_missing", "wrong_limit", "no_restart"])
def test_backoff_or_exit_137_alone_cannot_prove_oom(change):
    tools = IncidentTools("oom")
    c = tools.pods[0]["containers"][0]
    if change == "termination_missing":
        c["last_terminated_reason"] = "Error"
    elif change == "wrong_limit":
        c["resources"] = deepcopy(GOOD_MEMORY)
    else:
        c["restart_count"] = 0
    evidence = asyncio.run(collect_evidence(tools))
    candidate = asyncio.run(IncidentDiagnoser("oom").diagnose(evidence)).diagnosis
    assert validate_diagnosis(candidate, evidence).status == "inconclusive"


@pytest.mark.parametrize("change", ["ready_endpoint", "pods_not_ready", "selector_matches", "missing_pods_citation"])
def test_selector_diagnosis_requires_healthy_backend_and_missing_routing(change):
    tools = IncidentTools("bad_selector")
    if change == "ready_endpoint":
        tools.routing["endpoints"] = [{"pod_uid": "healthy-pod", "ready": True, "terminating": False}]
    elif change == "pods_not_ready":
        tools.pods[0]["containers"][0]["ready"] = False
    elif change == "selector_matches":
        tools.pods[0]["labels"] = deepcopy(BAD_SELECTOR)
    evidence = asyncio.run(collect_evidence(tools))
    candidate = asyncio.run(IncidentDiagnoser("bad_selector").diagnose(evidence)).diagnosis
    if change == "missing_pods_citation":
        candidate.evidence_ids = ["e1"]
    assert validate_diagnosis(candidate, evidence).status == "inconclusive"


def test_healthy_pods_cannot_hide_a_service_outage():
    evidence = asyncio.run(collect_evidence(IncidentTools("bad_selector")))
    candidate = Diagnosis(status="no_active_incident", incident_type="none", suspected_root_cause="Healthy pods",
                          confidence=1, evidence_ids=["e1", "e2"], alternative_hypotheses=[])
    assert validate_diagnosis(candidate, evidence).status == "inconclusive"


def action_runner(incident):
    service = RestrictedRepairRunner(client.ApiClient())
    dep = deployment()
    c = dep.spec.template.spec.containers[0]
    if incident != "bad_image":
        c.image = GOOD_IMAGE
    if incident == "crashloop":
        c.command = deepcopy(CRASH_COMMAND)
    if incident == "oom":
        c.resources = client.V1ResourceRequirements(**BAD_MEMORY)
    if incident == "bad_env":
        c.env[0].value = BAD_HOST
    service.apps = Mock()
    service.reader.core = Mock()
    service.reader.get_api_routing = Mock(return_value=IncidentTools(incident).routing)
    service.apps.read_namespaced_deployment.return_value = dep
    service.apps.patch_namespaced_deployment.return_value = SimpleNamespace(metadata=SimpleNamespace(uid="deployment-api", generation=6))
    service.reader.core.patch_namespaced_service.return_value = SimpleNamespace(metadata=SimpleNamespace(uid="service-api", resource_version="201"))
    return service


@pytest.mark.parametrize("incident", ACTIONS)
def test_actual_runner_changes_only_fixed_fixture_fields_and_blocks_reuse(incident):
    service = action_runner(incident)
    request = propose_repair(report(incident))
    action = getattr(service, ACTIONS[incident])
    result = action(request, f"approve {request.id}")
    target = service.reader.core.patch_namespaced_service if incident == "bad_selector" else service.apps.patch_namespaced_deployment
    target.assert_called_once()
    args = target.call_args.args
    assert args[:2] == ("api", "incidentpilot")
    assert args[2][:2] == [
        {"op": "test", "path": "/metadata/uid", "value": "service-api" if incident == "bad_selector" else "deployment-api"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "200" if incident == "bad_selector" else "100"},
    ]
    expected = fixed_patch(incident, service.apps.read_namespaced_deployment.return_value.spec.template.spec.containers[0], False)
    assert [op for op in args[2] if op["op"] != "test"] == expected
    if incident == "bad_selector":
        service.apps.patch_namespaced_deployment.assert_not_called()
        assert result["service_uid"] == "service-api"
        service.reader.get_api_routing.return_value["service"]["resource_version"] = "201"
    else:
        service.reader.core.patch_namespaced_service.assert_not_called()
        service.apps.read_namespaced_deployment.return_value.metadata.generation = 6
    with pytest.raises(RepairError, match="changed"):
        action(request, f"approve {request.id}")
    target.assert_called_once()


@pytest.mark.parametrize("incident", ACTIONS)
@pytest.mark.parametrize("change", ["expired", "tampered", "wrong_function", "mixed_fixture", "stale"])
def test_all_action_boundaries_reject_unsafe_or_stale_execution(incident, change):
    service = action_runner(incident)
    request = propose_repair(report(incident))
    action = getattr(service, ACTIONS[incident])
    if change == "expired":
        request = request.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        request = request.model_copy(update={"id": proposal_id(request)})
    elif change == "tampered":
        request = request.model_copy(update={"expected_effect": "Altered"})
    elif change == "wrong_function":
        action = service.restart_deployment if incident != "crashloop" else service.rollback_image_tag
    elif change == "mixed_fixture":
        service.apps.read_namespaced_deployment.return_value.spec.template.spec.containers[0].args = ["unexpected"]
    else:
        service.apps.read_namespaced_deployment.return_value.metadata.generation = 9
    with pytest.raises(RepairError):
        action(request, f"approve {request.id}")
    service.apps.patch_namespaced_deployment.assert_not_called()
    service.reader.core.patch_namespaced_service.assert_not_called()


def test_cross_action_parameter_tampering_is_rejected_even_with_recomputed_id():
    request = propose_repair(report("oom"))
    request = request.model_copy(update={"parameters": {"namespace": "incidentpilot", "memory_limit": "512Mi"}})
    request = request.model_copy(update={"id": proposal_id(request)})
    with pytest.raises(RepairError):
        check_approval(request, f"approve {request.id}")


@pytest.mark.parametrize("change", ["uid", "resource_version", "selector"])
def test_service_concurrent_edits_block_selector_patch(change):
    service = action_runner("bad_selector")
    request = propose_repair(report("bad_selector"))
    service.reader.get_api_routing.return_value["service"][change] = "different" if change != "selector" else {"app": "api"}
    with pytest.raises(RepairError, match="changed"):
        service.restore_known_service_selector(request, f"approve {request.id}")
    service.reader.core.patch_namespaced_service.assert_not_called()
