"""Five fixed repairs; approval never enables arbitrary patches or shell access."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import httpx
from kubernetes import client, config

from agent.diagnosis import validate_diagnosis
from agent.kubernetes import CONTEXT, NAMESPACE, ROOT, TIMEOUT, KubernetesReader
from agent.models import (
    RepairProposal, RepairParameters, RestartParameters, MemoryParameters, EnvParameters, SelectorParameters,
)
from agent.fixtures import (
    ACTIONS, BAD_HOST, BAD_IMAGE, BAD_SELECTOR, CRASH_COMMAND, GOOD_IMAGE, GOOD_MEMORY, BAD_MEMORY,
    GOOD_SELECTOR, fixture_matches, container_configuration,
)


class RepairError(RuntimeError):
    """Sanitized failure safe to include in an incident report."""


def proposal_id(proposal):
    payload = proposal.model_dump(mode="json", exclude={"id"}, warnings=False)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def propose_repair(report):
    diagnosis = validate_diagnosis(report.diagnosis, report.evidence)
    if diagnosis.status != "diagnosed":
        return None
    deployment = next(e.data["deployment"] for e in report.evidence if "deployment" in e.data)
    incident = diagnosis.incident_type
    if (deployment["name"] != "api" or deployment["desired_replicas"] != 1
        or len(deployment["containers"]) != 1 or not fixture_matches(deployment["containers"][0], incident)):
        return None
    data = next(e.data for e in report.evidence if "deployment" in e.data)
    service = data.get("service", {})
    if service.get("selector") != (BAD_SELECTOR if incident == "bad_selector" else GOOD_SELECTOR):
        return None
    types = {"bad_image": RepairParameters, "crashloop": RestartParameters, "oom": MemoryParameters,
             "bad_env": EnvParameters, "bad_selector": SelectorParameters}
    proposal = RepairProposal(
        id="", deployment_uid=deployment["uid"], generation=deployment["generation"],
        action=ACTIONS[incident], parameters=types[incident](),
        expected_image=BAD_IMAGE if incident == "bad_image" else GOOD_IMAGE,
        target_uid=service.get("uid") if incident == "bad_selector" else deployment["uid"],
        resource_version=service.get("resource_version") if incident == "bad_selector" else None,
        expected_effect={"bad_image": "Restore the known-good API image and complete the stalled rollout.",
                         "crashloop": "Restore the image's default startup command and restart the API rollout.",
                         "oom": "Restore the API memory request to 64Mi and limit to 128Mi.",
                         "bad_env": "Restore the non-secret DB_HOST value to postgres.",
                         "bad_selector": "Restore Service/api selector to app=api and recover ready endpoints."}[incident],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        evidence_ids=diagnosis.evidence_ids,
    )
    return proposal.model_copy(update={"id": proposal_id(proposal)})


def check_approval(proposal, approval):
    try:
        proposal = RepairProposal.model_validate(proposal.model_dump(warnings=False))
    except Exception:
        raise RepairError("Repair is outside the fixed action allowlist.") from None
    if (approval != f"approve {proposal.id}" or proposal.id != proposal_id(proposal)
        or datetime.now(timezone.utc) >= proposal.expires_at):
        raise RepairError("Approval is missing, mismatched, altered, or expired; collect a new proposal.")


class RestrictedRepairRunner:
    def __init__(self, api_client=None, transport=None):
        # Called lazily only after approval. Never fall back to admin or reader credentials.
        if api_client is None:
            path = ROOT / ".local" / "repair-kubeconfig"
            if not path.is_file():
                raise RepairError("Run scripts/setup-repair.ps1 to provision repair credentials.")
            configuration = client.Configuration()
            config.load_kube_config(config_file=str(path), context=CONTEXT,
                                   client_configuration=configuration, persist_config=False)
            api_client = client.ApiClient(configuration)
        self.reader = KubernetesReader(api_client)
        self.apps = self.reader.apps
        self.transport = transport

    def rollback_image_tag(self, proposal: RepairProposal, approval: str):
        return self.execute(proposal, approval, expected_action="rollback_image_tag")

    def restart_deployment(self, proposal, approval):
        return self.execute(proposal, approval, expected_action="restart_deployment")

    def increase_memory_limit_to_known_safe_value(self, proposal, approval):
        return self.execute(proposal, approval, expected_action="increase_memory_limit_to_known_safe_value")

    def restore_known_env_value(self, proposal, approval):
        return self.execute(proposal, approval, expected_action="restore_known_env_value")

    def restore_known_service_selector(self, proposal, approval):
        return self.execute(proposal, approval, expected_action="restore_known_service_selector")

    def execute(self, proposal, approval, *, expected_action):
        check_approval(proposal, approval)
        if proposal.action != expected_action:
            raise RepairError("Repair action does not match the selected allowlisted function.")
        incident = next(k for k, v in ACTIONS.items() if v == proposal.action)
        try:
            deployment = self.apps.read_namespaced_deployment("api", NAMESPACE, _request_timeout=TIMEOUT)
            containers = deployment.spec.template.spec.containers
            if (deployment.metadata.uid != proposal.deployment_uid
                or deployment.metadata.generation != proposal.generation
                or deployment.spec.replicas != 1 or len(containers) != 1
                or not fixture_matches({"name": containers[0].name, "image": containers[0].image,
                                        "image_pull_policy": containers[0].image_pull_policy,
                                        **container_configuration(containers[0])}, incident)):
                raise RepairError("Deployment changed since the proposal; collect a new diagnosis and approval.")
            routing = self.reader.get_api_routing()
            if routing["service"]["selector"] != (BAD_SELECTOR if incident == "bad_selector" else GOOD_SELECTOR):
                raise RepairError("Service changed since the proposal; collect a new diagnosis and approval.")
            if incident == "bad_selector":
                return self._restore_selector(proposal, approval, deployment, routing["service"])
            # Atomic preconditions close the read/patch race for the fixed target.
            body = [
                {"op": "test", "path": "/metadata/uid", "value": proposal.deployment_uid},
                {"op": "test", "path": "/metadata/resourceVersion", "value": deployment.metadata.resource_version},
            ]
            base = "/spec/template/spec/containers/0"
            if incident == "bad_image":
                body += [{"op": "test", "path": base + "/image", "value": BAD_IMAGE},
                         {"op": "replace", "path": base + "/image", "value": GOOD_IMAGE}]
            elif incident == "crashloop":
                body += [{"op": "test", "path": base + "/command", "value": CRASH_COMMAND},
                         {"op": "remove", "path": base + "/command"}]
            elif incident == "oom":
                body += [{"op": "test", "path": base + "/resources", "value": BAD_MEMORY},
                         {"op": "replace", "path": base + "/resources", "value": GOOD_MEMORY}]
            elif incident == "bad_env":
                indexes = [i for i, e in enumerate(containers[0].env or []) if e.name == "DB_HOST"]
                if len(indexes) != 1:
                    raise RepairError("DB_HOST fixture is ambiguous; collect a new diagnosis.")
                path = base + f"/env/{indexes[0]}"
                body += [{"op": "test", "path": path, "value": {"name": "DB_HOST", "value": BAD_HOST}},
                         {"op": "replace", "path": path + "/value", "value": "postgres"}]
            check_approval(proposal, approval)
            result = self.apps.patch_namespaced_deployment("api", NAMESPACE, body, _request_timeout=TIMEOUT)
            return {"action": proposal.action, "image": GOOD_IMAGE, "deployment_uid": result.metadata.uid,
                    "generation": result.metadata.generation}
        except RepairError:
            raise
        except Exception:
            raise RepairError("Repair rejected or unavailable; no automatic retry. Inspect the deployment and credentials.") from None

    def _restore_selector(self, proposal, approval, deployment, service):
        if service["uid"] != proposal.target_uid or service["resource_version"] != proposal.resource_version:
            raise RepairError("Service changed since the proposal; collect a new diagnosis and approval.")
        body = [
            {"op": "test", "path": "/metadata/uid", "value": proposal.target_uid},
            {"op": "test", "path": "/metadata/resourceVersion", "value": proposal.resource_version},
            {"op": "test", "path": "/spec/selector", "value": BAD_SELECTOR},
            {"op": "replace", "path": "/spec/selector", "value": GOOD_SELECTOR},
        ]
        check_approval(proposal, approval)
        result = self.reader.core.patch_namespaced_service("api", NAMESPACE, body, _request_timeout=TIMEOUT)
        return {"action": proposal.action, "deployment_uid": deployment.metadata.uid,
                "generation": deployment.metadata.generation, "service_uid": result.metadata.uid,
                "service_resource_version": result.metadata.resource_version}

    def verify(self, execution, timeout_seconds=90):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                deployment = self.reader.get_deployment(NAMESPACE, "api")["deployment"]
                pods = self.reader.get_pods(NAMESPACE)["pods"]
                rs_uids = {rs["uid"] for rs in deployment["replica_sets"]}
                owned = [p for p in pods if not p["terminating"] and any(
                    o["kind"] == "ReplicaSet" and o["uid"] in rs_uids and o["controller"] for o in p["owners"]
                )]
                if (deployment["uid"] != execution["deployment_uid"]
                    or deployment["generation"] != execution["generation"]
                    or deployment["desired_replicas"] != 1
                    or len(deployment["containers"]) != 1 or not fixture_matches(deployment["containers"][0], "none")):
                    raise RepairError("Deployment changed during verification; recovery is not confirmed.")
                routing = self.reader.get_api_routing()
                if (routing["service"]["selector"] != GOOD_SELECTOR
                    or (execution.get("service_uid") and routing["service"]["uid"] != execution["service_uid"])
                    or (execution.get("service_resource_version")
                        and routing["service"]["resource_version"] != execution["service_resource_version"])):
                    raise RepairError("Service changed during verification; recovery is not confirmed.")
                rollout = ((deployment["observed_generation"] or 0) >= deployment["generation"]
                           and all(deployment[key] == 1 for key in (
                               "replicas", "updated_replicas", "ready_replicas", "available_replicas"))
                           and len(owned) == 1 and owned[0]["phase"] == "Running"
                           and len(owned[0]["containers"]) == 1 and all(
                               c["name"] == "api" and c["image"] == GOOD_IMAGE and c["ready"]
                               and not c["waiting_reason"] and fixture_matches(c, "none") for c in owned[0]["containers"])
                           and {p["uid"] for p in owned} <= {e["pod_uid"] for e in routing["endpoints"]
                                                                           if e["ready"] and not e["terminating"]})
                if rollout:
                    with httpx.Client(timeout=5, transport=self.transport, trust_env=False) as http:
                        frontend = http.get("http://localhost:8080/")
                        health = http.get("http://localhost:8080/healthz")
                        message = http.get("http://localhost:8080/api/message")
                        for response in (frontend, health, message):
                            response.raise_for_status()
                        if ("IncidentPilot demo" in frontend.text
                            and health.json() == {"status": "healthy", "database": "reachable"}
                            and message.json() == {"source": "postgres", "message": "Hello from PostgreSQL. The demo application is healthy."}):
                            return {"healthy": True, "rollout": "complete", "ready_replicas": 1,
                                    "frontend": "reachable", "database": "reachable", "message": "verified",
                                    "verified_at": datetime.now(timezone.utc).isoformat()}
            except RepairError:
                raise
            except Exception:
                pass
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise RepairError("Recovery verification timed out; rollout and database-backed HTTP health did not both pass.")
