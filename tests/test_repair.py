import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("kubernetes")

import httpx
from kubernetes import client
from langgraph.types import Command

from agent.graph import build_graph
from agent.models import Diagnosis, ModelResult, RepairProposal
from agent.repair import (
    GOOD_IMAGE, RepairError, RestrictedRepairRunner, check_approval, proposal_id,
)
from test_diagnosis import FakeTools, diagnosis


class Diagnoser:
    model = "test"

    async def diagnose(self, evidence):
        return ModelResult(diagnosis=Diagnosis.model_validate(diagnosis()), model=self.model, usage={})


def proposal():
    result = RepairProposal(id="", deployment_uid="deployment-api", generation=5,
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                            evidence_ids=["e1", "e3", "e4"])
    return result.model_copy(update={"id": proposal_id(result)})


def test_graph_cannot_construct_runner_or_write_before_exact_approval():
    factory = Mock()
    factory.return_value.rollback_image_tag.return_value = {"generation": 6}
    factory.return_value.verify.return_value = {"healthy": True}

    async def check():
        graph = build_graph(FakeTools(), Diagnoser(), repair=True, runner_factory=factory)
        config = {"configurable": {"thread_id": "approved"}}
        pending = await graph.ainvoke({}, config)
        assert pending["__interrupt__"]
        assert pending["report"].approval == "pending"
        assert pending["report"].proposal.evidence_ids == ["e1", "e3", "e4"]
        assert pending["report"].proposal.parameters.image == GOOD_IMAGE
        factory.assert_not_called()
        result = await graph.ainvoke(Command(resume=f"approve {pending['report'].proposal.id}"), config)
        assert result["report"].outcome == "recovered"
        assert result["report"].stages == ["COLLECT", "HYPOTHESIZE", "TEST", "PROPOSE_FIX",
                                            "WAIT_FOR_APPROVAL", "EXECUTE", "VERIFY", "REPORT"]
        factory.return_value.rollback_image_tag.assert_called_once()
        # Completed graph cannot re-execute on repeated resume.
        await graph.ainvoke(Command(resume=f"approve {pending['report'].proposal.id}"), config)
        factory.return_value.rollback_image_tag.assert_called_once()
    asyncio.run(check())


@pytest.mark.parametrize("answer", ["deny", "", True, {"approved": True}, "approve wrong-id"])
def test_denial_and_mismatched_approval_never_construct_runner(answer):
    factory = Mock()

    async def check():
        graph = build_graph(FakeTools(), Diagnoser(), repair=True, runner_factory=factory)
        config = {"configurable": {"thread_id": "denied"}}
        await graph.ainvoke({}, config)
        result = await graph.ainvoke(Command(resume=answer), config)
        assert result["report"].approval == "denied"
        assert result["report"].outcome == "denied"
        factory.assert_not_called()
    asyncio.run(check())


@pytest.mark.parametrize("change", ["healthy", "other_image", "failed_read"])
def test_unsupported_diagnosis_never_requests_approval(change):
    tools = FakeTools(healthy=change == "healthy")
    if change == "other_image":
        tools.deployment["containers"][0]["image"] = "unapproved:v1"
    if change == "failed_read":
        tools.failure = "get_deployment"
    factory = Mock()
    result = asyncio.run(build_graph(tools, Diagnoser(), repair=True, runner_factory=factory).ainvoke(
        {}, {"configurable": {"thread_id": change}}))
    assert "__interrupt__" not in result
    assert result["report"].proposal is None
    factory.assert_not_called()


def test_healthy_run_has_no_proposal_and_no_runner():
    class HealthyDiagnoser(Diagnoser):
        async def diagnose(self, evidence):
            return ModelResult(diagnosis=Diagnosis.model_validate(
                diagnosis("no_active_incident", ["e1", "e2"])), model=self.model, usage={})

    factory = Mock()
    result = asyncio.run(build_graph(FakeTools(healthy=True), HealthyDiagnoser(), repair=True,
                                    runner_factory=factory).ainvoke(
        {}, {"configurable": {"thread_id": "healthy"}}))
    assert result["report"].outcome == "not_needed"
    assert result["report"].approval == "not_requested"
    factory.assert_not_called()


def test_approval_expiring_at_gate_blocks_runner(monkeypatch):
    factory = Mock()

    async def check():
        graph = build_graph(FakeTools(), Diagnoser(), repair=True, runner_factory=factory)
        config = {"configurable": {"thread_id": "expired"}}
        pending = await graph.ainvoke({}, config)
        class Later:
            @staticmethod
            def now(tz):
                return pending["report"].proposal.expires_at + timedelta(seconds=1)
        monkeypatch.setattr("agent.repair.datetime", Later)
        result = await graph.ainvoke(Command(resume=f"approve {pending['report'].proposal.id}"), config)
        assert result["report"].outcome == "blocked"
        assert "expired" in result["report"].error
        factory.assert_not_called()
    asyncio.run(check())


@pytest.mark.parametrize("stage", ["execute", "verify"])
def test_execution_and_verification_failures_are_reported_without_retry(stage):
    factory = Mock()
    factory.return_value.rollback_image_tag.return_value = {"generation": 6}
    getattr(factory.return_value, "rollback_image_tag" if stage == "execute" else "verify").side_effect = RepairError("Stopped safely")

    async def check():
        graph = build_graph(FakeTools(), Diagnoser(), repair=True, runner_factory=factory)
        config = {"configurable": {"thread_id": stage}}
        pending = await graph.ainvoke({}, config)
        result = await graph.ainvoke(Command(resume=f"approve {pending['report'].proposal.id}"), config)
        assert result["report"].outcome == "failed"
        assert result["report"].error == "Stopped safely"
        factory.return_value.rollback_image_tag.assert_called_once()
        assert ("VERIFY" in result["report"].stages) == (stage == "verify")
    asyncio.run(check())


def deployment():
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(uid="deployment-api", generation=5, resource_version="100"),
        spec=client.V1DeploymentSpec(replicas=1, selector=client.V1LabelSelector(match_labels={"app": "api"}),
            template=client.V1PodTemplateSpec(spec=client.V1PodSpec(containers=[
                client.V1Container(name="api", image="incidentpilot-api:m1-does-not-exist", image_pull_policy="Never")]))),
    )


def runner():
    result = RestrictedRepairRunner(client.ApiClient())
    result.apps = Mock()
    result.apps.read_namespaced_deployment.return_value = deployment()
    result.apps.patch_namespaced_deployment.return_value = SimpleNamespace(
        metadata=SimpleNamespace(uid="deployment-api", generation=6))
    return result


@pytest.mark.parametrize("change", ["none", "uid", "generation", "image", "replicas", "policy", "container", "extra"])
def test_runner_only_patches_exact_fixture_and_atomic_preconditions(change):
    service = runner()
    dep = service.apps.read_namespaced_deployment.return_value
    if change == "uid":
        dep.metadata.uid = "replacement"
    elif change == "generation":
        dep.metadata.generation = 6
    elif change == "image":
        dep.spec.template.spec.containers[0].image = GOOD_IMAGE
    elif change == "replicas":
        dep.spec.replicas = 2
    elif change == "policy":
        dep.spec.template.spec.containers[0].image_pull_policy = "Always"
    elif change == "container":
        dep.spec.template.spec.containers[0].name = "sidecar"
    elif change == "extra":
        dep.spec.template.spec.containers.append(client.V1Container(name="sidecar", image="anything"))
    request = proposal()
    if change != "none":
        with pytest.raises(RepairError, match="changed"):
            service.rollback_image_tag(request, f"approve {request.id}")
        service.apps.patch_namespaced_deployment.assert_not_called()
    else:
        assert service.rollback_image_tag(request, f"approve {request.id}")["generation"] == 6
        args = service.apps.patch_namespaced_deployment.call_args.args
        assert args[:2] == ("api", "incidentpilot")
        assert args[2] == [
            {"op": "test", "path": "/metadata/uid", "value": "deployment-api"},
            {"op": "test", "path": "/metadata/resourceVersion", "value": "100"},
            {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": "incidentpilot-api:m1-does-not-exist"},
            {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": GOOD_IMAGE},
        ]


@pytest.mark.parametrize("change", ["expired", "tampered", "other_action", "other_parameters", "denied"])
def test_runner_rejects_invalid_approval_before_cluster_access(change):
    request = proposal()
    answer = f"approve {request.id}"
    if change == "expired":
        request = request.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        request = request.model_copy(update={"id": proposal_id(request)})
        answer = f"approve {request.id}"
    elif change == "tampered":
        request = request.model_copy(update={"generation": 9})
    elif change == "other_action":
        request = request.model_copy(update={"action": "delete_namespace"})
    elif change == "other_parameters":
        request = request.model_copy(update={"parameters": {"namespace": "kube-system", "image": "evil"}})
    else:
        answer = "deny"
    service = runner()
    with pytest.raises(RepairError):
        service.rollback_image_tag(request, answer)
    service.apps.read_namespaced_deployment.assert_not_called()
    service.apps.patch_namespaced_deployment.assert_not_called()


def test_patch_conflict_is_sanitized_and_never_retried():
    service = runner()
    service.apps.patch_namespaced_deployment.side_effect = RuntimeError("credential details")
    request = proposal()
    with pytest.raises(RepairError, match="no automatic retry") as error:
        service.rollback_image_tag(request, f"approve {request.id}")
    assert "credential details" not in str(error.value)
    service.apps.patch_namespaced_deployment.assert_called_once()


@pytest.mark.parametrize("case", ["healthy", "stalled", "bad_pod", "database_down", "changed"])
def test_verification_requires_owned_ready_baseline_rollout_and_database(case, monkeypatch):
    service = runner()
    tools = FakeTools(healthy=True)
    tools.deployment["generation"] = tools.deployment["observed_generation"] = 6
    service.reader = Mock()
    service.reader.get_deployment.return_value = {"deployment": tools.deployment}
    service.reader.get_pods.return_value = {"pods": tools.pods}
    if case == "stalled":
        tools.deployment["replicas"] = 2
    elif case == "bad_pod":
        tools.pods[0]["containers"][0]["ready"] = False
    elif case == "changed":
        tools.deployment["generation"] = 7

    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text="IncidentPilot demo")
        if request.url.path == "/healthz":
            return httpx.Response(503 if case == "database_down" else 200,
                                  json={"status": "healthy", "database": "reachable"})
        return httpx.Response(200, json={"source": "postgres", "message": "Hello from PostgreSQL. The demo application is healthy."})

    service.transport = httpx.MockTransport(respond)
    clock = iter([0, 0, 10, 10])
    monkeypatch.setattr("agent.repair.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("agent.repair.time.sleep", lambda seconds: None)
    execution = {"deployment_uid": "deployment-api", "generation": 6}
    if case == "healthy":
        assert service.verify(execution, timeout_seconds=1)["healthy"] is True
        assert calls == ["/", "/healthz", "/api/message"]
    else:
        with pytest.raises(RepairError):
            service.verify(execution, timeout_seconds=1)
        if case in {"stalled", "bad_pod", "changed"}:
            assert not calls


def test_missing_repair_config_never_falls_back_to_admin(monkeypatch, tmp_path):
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "kubeconfig").write_text("admin")
    monkeypatch.setattr("agent.repair.ROOT", tmp_path)
    with pytest.raises(RepairError, match="setup-repair"):
        RestrictedRepairRunner()
