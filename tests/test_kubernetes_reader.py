from unittest.mock import MagicMock
import json

import pytest

pytest.importorskip("kubernetes")

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from agent.kubernetes import KubernetesReader
from agent.fixtures import CRASH_COMMAND, BAD_MEMORY


@pytest.fixture
def reader():
    backend = KubernetesReader(api_client=MagicMock())
    backend.core = MagicMock()
    backend.apps = MagicMock()
    return backend


def k8s_pod(started=False):
    return client.V1Pod(
        metadata=client.V1ObjectMeta(name="api-test", uid="pod-uid", labels={"app": "api"}),
        spec=client.V1PodSpec(containers=[client.V1Container(name="api", image="demo:tag", image_pull_policy="Never")]),
        status=client.V1PodStatus(phase="Running" if started else "Pending", container_statuses=[
            client.V1ContainerStatus(name="api", image="demo:tag", image_id="", ready=started,
                                     restart_count=0, container_id="containerd://id" if started else None,
                                     state=client.V1ContainerState(waiting=None if started else client.V1ContainerStateWaiting(reason="ErrImageNeverPull"))),
        ]),
    )


def pod_list(*pods, continuation=None):
    return client.V1PodList(items=list(pods), metadata=client.V1ListMeta(_continue=continuation))


def test_reader_rejects_other_namespace_before_any_api_call(reader):
    for name, args in [("get_pods", ("default",)), ("describe_pod", ("default", "api")),
                       ("get_events", ("kube-system",)), ("get_deployment", ("default", "api"))]:
        with pytest.raises(ValueError, match="namespace"):
            getattr(reader, name)(*args)
    assert not reader.core.mock_calls
    assert not reader.apps.mock_calls


def test_reader_rejects_log_inputs_before_querying(reader):
    for args in [("../../secrets", 10), ("api", 0), ("api", 61), ("api", 10, "x" * 201)]:
        with pytest.raises(ValueError):
            reader.search_logs(*args)
    assert not reader.core.mock_calls


def test_unstarted_container_logs_are_explicitly_absent(reader):
    reader.core.list_namespaced_pod.return_value = pod_list(k8s_pod())
    result = reader.search_logs("api", 10)
    assert result["entries"][0]["status"] == "not_started"
    assert result["entries"][0]["pod_uid"] == "pod-uid"
    reader.core.read_namespaced_pod_log.assert_not_called()


def test_log_reads_have_time_line_byte_and_request_bounds(reader):
    reader.core.list_namespaced_pod.return_value = pod_list(k8s_pod(started=True))
    reader.core.read_namespaced_pod_log.return_value = "timestamp first\ntimestamp useful line\n"
    result = reader.search_logs("api", 5, "useful")
    assert result["entries"][0]["lines"] == ["timestamp useful line"]
    parameters = reader.core.read_namespaced_pod_log.call_args.kwargs
    assert parameters["since_seconds"] == 300
    assert parameters["tail_lines"] == 100
    assert parameters["limit_bytes"] == 8192
    assert parameters["follow"] is False
    assert parameters["_request_timeout"] == (3, 10)


def test_failed_log_read_is_not_presented_as_an_empty_success(reader):
    reader.core.list_namespaced_pod.return_value = pod_list(k8s_pod(started=True))
    reader.core.read_namespaced_pod_log.side_effect = ApiException(status=403, reason="sensitive server details")
    result = reader.search_logs("api", 5)
    assert result["entries"][0]["status"] == "unavailable"
    assert result["entries"][0]["http_status"] == 403
    assert "sensitive" not in str(result)


def test_incomplete_paginated_pod_read_is_rejected(reader):
    reader.core.list_namespaced_pod.return_value = pod_list(k8s_pod(), continuation="next-page")
    with pytest.raises(RuntimeError, match="incomplete snapshot"):
        reader.get_pods("incidentpilot")


def test_pod_projection_omits_environment_and_secret_references(reader):
    pod = k8s_pod()
    pod.spec.containers[0].env = [client.V1EnvVar(name="PASSWORD", value="do-not-share")]
    reader.core.read_namespaced_pod.return_value = pod
    result = reader.describe_pod("incidentpilot", "api-test")
    assert result["pod"]["uid"] == "pod-uid"
    assert "PASSWORD" not in str(result)
    assert "do-not-share" not in str(result)


def test_projection_exposes_only_known_host_values_and_classifies_commands(reader):
    pod = k8s_pod()
    c = pod.spec.containers[0]
    c.command = CRASH_COMMAND
    c.env = [client.V1EnvVar(name="DB_HOST", value="postgres-m4-missing"),
             client.V1EnvVar(name="DB_PASSWORD", value="never-export"),
             client.V1EnvVar(name="DB_USER", value_from=client.V1EnvVarSource(
                 secret_key_ref=client.V1SecretKeySelector(name="private-secret", key="private-key")))]
    c.resources = client.V1ResourceRequirements(**BAD_MEMORY)
    projected = reader.pod_data(pod)
    assert projected["containers"][0]["db_host"] == "postgres-m4-missing"
    assert projected["containers"][0]["startup_profile"] == "crashloop_fixture"
    assert projected["containers"][0]["resources"] == BAD_MEMORY
    assert "never-export" not in str(projected)
    assert "private-secret" not in str(projected)
    c.env[0].value = "user-private-host"
    c.command = ["python", "private-command"]
    projected = reader.pod_data(pod)
    assert projected["containers"][0]["db_host"] == "redacted_or_missing"
    assert projected["containers"][0]["startup_profile"] == "custom"
    assert "user-private-host" not in str(projected)
    assert "private-command" not in str(projected)


def test_last_oom_termination_is_retained_without_termination_message(reader):
    pod = k8s_pod(started=True)
    status = pod.status.container_statuses[0]
    status.last_state = client.V1ContainerState(terminated=client.V1ContainerStateTerminated(
        exit_code=137, reason="OOMKilled", message="private-termination-message"))
    status.restart_count = 2
    c = reader.pod_data(pod)["containers"][0]
    assert c["last_terminated_reason"] == "OOMKilled"
    assert c["last_exit_code"] == 137
    assert "private-termination-message" not in str(c)


def test_crashloop_reads_previous_container_logs_even_without_container_id(reader):
    pod = k8s_pod()
    status = pod.status.container_statuses[0]
    status.state.waiting.reason = "CrashLoopBackOff"
    status.last_state = client.V1ContainerState(terminated=client.V1ContainerStateTerminated(exit_code=1, reason="Error"))
    reader.core.list_namespaced_pod.return_value = pod_list(pod)
    reader.core.read_namespaced_pod_log.return_value = "timestamp IncidentPilot crashloop fixture: startup failed"
    result = reader.search_logs("api", 10)
    assert result["entries"][0]["status"] == "available"
    assert reader.core.read_namespaced_pod_log.call_args.kwargs["previous"] is True


def test_routing_ignores_endpointslices_not_owned_by_current_service(reader):
    reader.discovery = MagicMock()
    reader.core.read_namespaced_service.return_value = client.V1Service(
        metadata=client.V1ObjectMeta(name="api", uid="service-current", resource_version="10"),
        spec=client.V1ServiceSpec(selector={"app": "api"}, ports=[client.V1ServicePort(port=8000, target_port="http")]))
    def endpoint_slice(owner):
        return client.V1EndpointSlice(address_type="IPv4", metadata=client.V1ObjectMeta(
            owner_references=[client.V1OwnerReference(api_version="v1", kind="Service", name="api", uid=owner, controller=True)]),
            endpoints=[client.V1Endpoint(addresses=["10.0.0.1"], conditions=client.V1EndpointConditions(ready=True),
                                         target_ref=client.V1ObjectReference(kind="Pod", uid=owner + "-pod"))])
    slices = client.ApiClient().sanitize_for_serialization(client.V1EndpointSliceList(
        items=[endpoint_slice("service-current"), endpoint_slice("service-old")], metadata=client.V1ListMeta()))
    response = reader.discovery.list_namespaced_endpoint_slice.return_value
    response.data = json.dumps(slices).encode()
    data = reader.get_api_routing()
    assert data["endpoints"] == [{"pod_uid": "service-current-pod", "ready": True, "terminating": False}]
    assert data["service"]["resource_version"] == "10"
    assert reader.discovery.list_namespaced_endpoint_slice.call_args.kwargs["limit"] == 100
    slices["items"][0]["endpoints"] = None
    response.data = json.dumps(slices).encode()
    assert reader.get_api_routing()["endpoints"] == []
    assert response.release_conn.call_count == 2
