import asyncio
from copy import deepcopy
import json

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("dotenv")

import httpx

from agent.diagnosis import OpenRouterDiagnoser
from agent.graph import build_graph
from agent.models import Diagnosis

IMAGE = "incidentpilot-api:m1-does-not-exist"
GOOD_IMAGE = "incidentpilot-api:m0"


def container(image=IMAGE, waiting="ErrImageNeverPull", ready=False):
    return {"name": "api", "image": image, "image_pull_policy": "Never", "ready": ready,
            "restart_count": 0, "waiting_reason": waiting, "waiting_message": None}


def pod(uid="failed-pod", image=IMAGE, waiting="ErrImageNeverPull", ready=False):
    return {"name": f"api-{uid}", "uid": uid, "labels": {"app": "api"},
            "terminating": False, "phase": "Pending" if waiting else "Running",
            "owners": [{"kind": "ReplicaSet", "name": "api-new", "uid": "rs-new", "controller": True}],
            "containers": [container(image, waiting, ready)]}


class FakeTools:
    def __init__(self, healthy=False):
        self.calls = []
        self.failure = None
        self.pods = [pod()]
        self.deployment = {
            "name": "api", "uid": "deployment-api", "generation": 5, "observed_generation": 5,
            "replicas": 2, "desired_replicas": 1, "updated_replicas": 1,
            "ready_replicas": 1, "available_replicas": 1,
            "containers": [{"name": "api", "image": IMAGE, "image_pull_policy": "Never"}],
            "replica_sets": [{"name": "api-new", "uid": "rs-new", "containers": [{"name": "api", "image": IMAGE}]}],
        }
        self.events = [{"uid": "event-1", "type": "Warning", "reason": "ErrImageNeverPull",
                        "message": f'Container image "{IMAGE}" is not present with pull policy of Never',
                        "pod_uid": "failed-pod", "resource_kind": "Pod", "timestamp": "2026-10-06T10:00:00Z"}]
        if healthy:
            self.pods = [pod("healthy-pod", GOOD_IMAGE, None, True)]
            self.deployment["containers"][0]["image"] = GOOD_IMAGE
            self.deployment["replicas"] = 1

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self.failure == name:
            raise RuntimeError("Credential details must never be echoed")
        data = {"collected_at": "2026-10-06T10:00:01Z"}
        if name == "get_deployment":
            data["deployment"] = deepcopy(self.deployment)
        elif name == "get_pods":
            data["pods"] = deepcopy(self.pods)
        elif name == "get_events":
            data["events"] = deepcopy(self.events)
        elif name == "describe_pod":
            data["pod"] = deepcopy(next(p for p in self.pods if p["name"] == arguments["pod"]))
        elif name == "search_logs":
            data["entries"] = [{"pod_uid": "failed-pod", "status": "not_started", "lines": []}]
        else:
            raise AssertionError(f"Unexpected tool {name}")
        return data


def diagnosis(status="diagnosed", evidence_ids=None):
    return {
        "status": status,
        "incident_type": {"diagnosed": "bad_image", "no_active_incident": "none", "inconclusive": "unknown"}[status],
        "suspected_root_cause": f"API references absent image {IMAGE}; Never prevents pulling it.",
        "confidence": 0.95, "evidence_ids": evidence_ids or ["e1", "e3", "e4"],
        "alternative_hypotheses": [],
    }


def model_response(content):
    return {"model": "openai/gpt-4.1-mini", "usage": {"prompt_tokens": 500, "completion_tokens": 90, "total_tokens": 590},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]}


def run_graph(tools, output=None, http_status=200):
    def respond(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["provider"]["require_parameters"] is True
        assert "tools" not in payload
        evidence = json.loads(payload["messages"][1]["content"])["evidence"]
        assert all("observation" not in item for item in evidence)
        assert {e["source"] for e in evidence} == {"kubernetes", "events", "logs"}
        assert "Credential details" not in json.dumps(evidence)
        return httpx.Response(http_status, json=model_response(output or diagnosis()))

    model = OpenRouterDiagnoser("test-key", transport=httpx.MockTransport(respond))
    return asyncio.run(build_graph(tools, model).ainvoke({}))["report"]


def test_bad_image_has_cited_live_resource_evidence_and_no_execution():
    tools = FakeTools()
    report = run_graph(tools)
    assert report.stages == ["COLLECT", "HYPOTHESIZE"]
    assert report.diagnosis.status == "diagnosed"
    assert report.diagnosis.incident_type == "bad_image"
    by_id = {e.id: e for e in report.evidence}
    cited = [by_id[i] for i in report.diagnosis.evidence_ids]
    assert {e.source for e in cited} == {"kubernetes", "events"}
    assert IMAGE in report.diagnosis.suspected_root_cause
    assert report.usage["total_tokens"] == 590
    assert [name for name, _ in tools.calls] == [
        "get_deployment", "get_pods", "get_events", "describe_pod", "search_logs",
    ]
    assert "repair" not in report.model_dump()


@pytest.mark.parametrize("change", [
    "wrong_event_uid", "historical_event", "wrong_event_image", "missing_warning",
    "different_template", "different_pull_policy", "terminating", "unowned", "different_waiting_reason",
])
def test_bad_image_claim_is_rejected_without_matching_current_evidence(change):
    tools = FakeTools()
    if change == "wrong_event_uid":
        tools.events[0]["pod_uid"] = "unrelated"
    elif change == "historical_event":
        tools.pods[0]["uid"] = "new-pod-uid"
    elif change == "wrong_event_image":
        tools.events[0]["message"] = "Other image is absent"
    elif change == "missing_warning":
        tools.events[0]["type"] = "Normal"
    elif change == "different_template":
        tools.deployment["containers"][0]["image"] = GOOD_IMAGE
    elif change == "different_pull_policy":
        tools.deployment["containers"][0]["image_pull_policy"] = "Always"
    elif change == "terminating":
        tools.pods[0]["terminating"] = True
    elif change == "unowned":
        tools.pods[0]["owners"][0]["uid"] = "other-deployment-rs"
    elif change == "different_waiting_reason":
        tools.pods[0]["containers"][0]["waiting_reason"] = "ImagePullBackOff"
    assert run_graph(tools).diagnosis.status == "inconclusive"


@pytest.mark.parametrize("citations", [["e1", "e3", "invented"], ["e1", "e4"], ["e1", "e3"]])
def test_model_must_cite_deployment_failed_pod_and_warning(citations):
    assert run_graph(FakeTools(), diagnosis(evidence_ids=citations)).diagnosis.status == "inconclusive"


def test_recovered_rollout_does_not_reactivate_historical_warning():
    tools = FakeTools(healthy=True)
    report = run_graph(tools, diagnosis("no_active_incident", ["e1", "e2"]))
    assert report.diagnosis.status == "no_active_incident"
    event_evidence = next(e for e in report.evidence if e.source == "events")
    assert event_evidence.data["events"] == []
    assert event_evidence.data["excluded_other_resource_events"] == 1
    assert run_graph(FakeTools(healthy=True)).diagnosis.status == "inconclusive"


def test_old_healthy_replica_does_not_make_stalled_rollout_healthy():
    tools = FakeTools()
    tools.pods.append(pod("old-pod", GOOD_IMAGE, None, True))
    report = run_graph(tools, diagnosis("no_active_incident", ["e1", "e2"]))
    assert report.diagnosis.status == "inconclusive"


@pytest.mark.parametrize("tool", ["get_deployment", "get_pods", "get_events", "describe_pod"])
def test_required_read_failure_is_reported_as_inconclusive(tool):
    tools = FakeTools()
    tools.failure = tool
    report = run_graph(tools)
    assert report.diagnosis.status == "inconclusive"
    assert any(e.error for e in report.evidence)


def test_logs_failure_does_not_hide_direct_image_evidence():
    tools = FakeTools()
    tools.failure = "search_logs"
    assert run_graph(tools).diagnosis.status == "diagnosed"


@pytest.mark.parametrize("status", [401, 429, 503])
def test_provider_failure_cannot_become_a_supported_diagnosis(status):
    report = run_graph(FakeTools(), http_status=status)
    assert report.diagnosis.status == "inconclusive"
    assert report.usage == {}


def test_model_schema_rejects_actions_invalid_confidence_and_duplicate_ids():
    for update in ({"action": "restart_deployment"}, {"confidence": 1.5}, {"evidence_ids": ["e1", "e1"]}):
        with pytest.raises(ValueError):
            Diagnosis.model_validate({**diagnosis(), **update})


def test_env_file_is_used_without_overriding_process_configuration(monkeypatch, tmp_path):
    from agent import diagnosis as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=file-key\nOPENROUTER_MODEL=file-model\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "process-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    configured = OpenRouterDiagnoser.from_env()
    assert configured.api_key == "process-key"
    assert configured.model == "file-model"
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(RuntimeError, match="Paste your OpenRouter key"):
        OpenRouterDiagnoser.from_env()
