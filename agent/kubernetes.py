"""Bounded Kubernetes reads; no shell, mutation, or secret reads."""

from datetime import datetime, timezone
from pathlib import Path
import re

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "incidentpilot"
CONTEXT = "kind-incidentpilot"
SERVICES = {"api", "frontend", "postgres", "prometheus"}
TIMEOUT = (3, 10)


def require_namespace(namespace: str):
    if namespace != NAMESPACE:
        raise ValueError("Only the incidentpilot demo namespace is allowed")


def require_name(name: str):
    if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?", name):
        raise ValueError("Invalid Kubernetes resource name")


class KubernetesReader:
    def __init__(self, api_client=None):
        if api_client is None:
            path = ROOT / ".local" / "diagnosis-kubeconfig"
            if not path.is_file():
                raise RuntimeError("Run scripts/setup-diagnosis.ps1 to provision read-only credentials")
            configuration = client.Configuration()
            config.load_kube_config(
                config_file=str(path), context=CONTEXT,
                client_configuration=configuration, persist_config=False,
            )
            api_client = client.ApiClient(configuration)
        self.core = client.CoreV1Api(api_client)
        self.apps = client.AppsV1Api(api_client)

    @staticmethod
    def result(**data):
        return {"collected_at": datetime.now(timezone.utc).isoformat(), **data}

    @staticmethod
    def items(response):
        if response.metadata and response.metadata._continue:
            raise RuntimeError("Demo read exceeded 100 resources; refusing an incomplete snapshot")
        return response.items

    @staticmethod
    def pod_data(pod):
        statuses = {s.name: s for s in pod.status.container_statuses or []}
        containers = []
        for container in pod.spec.containers:
            status = statuses.get(container.name)
            waiting = status.state.waiting if status and status.state else None
            containers.append({
                "name": container.name, "image": container.image,
                "image_pull_policy": container.image_pull_policy,
                "ready": status.ready if status else False,
                "restart_count": status.restart_count if status else 0,
                "waiting_reason": waiting.reason if waiting else None,
                "waiting_message": waiting.message if waiting else None,
            })
        return {
            "name": pod.metadata.name, "uid": pod.metadata.uid,
            "labels": pod.metadata.labels or {},
            "terminating": pod.metadata.deletion_timestamp is not None,
            "owners": [{"kind": o.kind, "name": o.name, "uid": o.uid, "controller": o.controller}
                       for o in pod.metadata.owner_references or []],
            "phase": pod.status.phase, "containers": containers,
        }

    def get_pods(self, namespace: str):
        require_namespace(namespace)
        response = self.core.list_namespaced_pod(namespace, limit=100, _request_timeout=TIMEOUT)
        return self.result(pods=[self.pod_data(p) for p in self.items(response)])

    def describe_pod(self, namespace: str, pod: str):
        require_namespace(namespace)
        require_name(pod)
        return self.result(pod=self.pod_data(self.core.read_namespaced_pod(
            pod, namespace, _request_timeout=TIMEOUT,
        )))

    def get_events(self, namespace: str):
        require_namespace(namespace)
        events = []
        continuation = None
        for _ in range(5):
            response = self.core.list_namespaced_event(
                namespace, limit=100, _continue=continuation, _request_timeout=TIMEOUT,
            )
            events.extend(response.items)
            continuation = response.metadata._continue if response.metadata else None
            if not continuation:
                break
        if continuation:
            raise RuntimeError("Demo event read exceeded 500 resources; refusing an incomplete snapshot")
        return self.result(events=[{
            "uid": event.metadata.uid, "type": event.type, "reason": event.reason,
            "message": event.message, "pod_uid": event.involved_object.uid,
            "resource_kind": event.involved_object.kind,
            "timestamp": (event.last_timestamp or event.event_time or event.metadata.creation_timestamp).isoformat(),
        } for event in events])

    def get_deployment(self, namespace: str, name: str):
        require_namespace(namespace)
        if name not in SERVICES:
            raise ValueError("Only demo deployments are allowed")
        deployment = self.apps.read_namespaced_deployment(name, namespace, _request_timeout=TIMEOUT)
        replicas = self.apps.list_namespaced_replica_set(namespace, limit=100, _request_timeout=TIMEOUT)
        owned = [rs for rs in self.items(replicas) if (rs.spec.replicas or 0) > 0 and any(
            o.kind == "Deployment" and o.uid == deployment.metadata.uid and o.controller
            for o in rs.metadata.owner_references or []
        )]
        status = deployment.status
        return self.result(deployment={
            "name": name, "uid": deployment.metadata.uid, "generation": deployment.metadata.generation,
            "observed_generation": status.observed_generation,
            "replicas": status.replicas or 0, "desired_replicas": deployment.spec.replicas,
            "updated_replicas": status.updated_replicas or 0,
            "ready_replicas": status.ready_replicas or 0, "available_replicas": status.available_replicas or 0,
            "containers": [{"name": c.name, "image": c.image, "image_pull_policy": c.image_pull_policy}
                           for c in deployment.spec.template.spec.containers],
            "replica_sets": [{"name": rs.metadata.name, "uid": rs.metadata.uid,
                              "containers": [{"name": c.name, "image": c.image}
                                             for c in rs.spec.template.spec.containers]}
                             for rs in owned],
        })

    def search_logs(self, service: str, since_minutes: int, contains: str | None = None):
        if service not in SERVICES:
            raise ValueError("Only demo services are allowed")
        if not 1 <= since_minutes <= 60:
            raise ValueError("since_minutes must be between 1 and 60")
        if contains is not None and len(contains) > 200:
            raise ValueError("contains must be at most 200 characters")
        response = self.core.list_namespaced_pod(
            NAMESPACE, label_selector=f"app={service}", limit=100, _request_timeout=TIMEOUT,
        )
        pods = [p for p in self.items(response) if not p.metadata.deletion_timestamp]
        if len(pods) > 10:
            raise RuntimeError("Demo log read exceeded 10 pods")
        entries = []
        for pod in pods:
            statuses = {s.name: s for s in pod.status.container_statuses or []}
            for container in pod.spec.containers:
                status = statuses.get(container.name)
                entry = {"pod": pod.metadata.name, "pod_uid": pod.metadata.uid, "container": container.name}
                if not status or (status.state and status.state.waiting and not status.container_id):
                    entries.append({**entry, "status": "not_started", "lines": []})
                    continue
                try:
                    logs = self.core.read_namespaced_pod_log(
                        pod.metadata.name, NAMESPACE, container=container.name,
                        since_seconds=since_minutes * 60, tail_lines=100, limit_bytes=8192,
                        timestamps=True, follow=False, _request_timeout=TIMEOUT,
                    )
                    lines = [line for line in logs.splitlines() if contains is None or contains in line]
                    entries.append({**entry, "status": "available", "lines": lines})
                except ApiException as error:
                    entries.append({**entry, "status": "unavailable", "http_status": error.status, "lines": []})
        return self.result(service=service, since_minutes=since_minutes, entries=entries,
                           bounds={"tail_lines_per_container": 100, "bytes_per_container": 8192})
