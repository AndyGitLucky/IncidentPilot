from unittest.mock import MagicMock

import pytest

pytest.importorskip("kubernetes")

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from agent.kubernetes import KubernetesReader


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
