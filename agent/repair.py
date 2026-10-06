"""A single fixed repair; approval never enables arbitrary patches or shell access."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import httpx
from kubernetes import client, config

from agent.diagnosis import validate_diagnosis
from agent.kubernetes import CONTEXT, NAMESPACE, ROOT, TIMEOUT, KubernetesReader
from agent.models import RepairProposal

GOOD_IMAGE = "incidentpilot-api:m0"
BAD_IMAGE = "incidentpilot-api:m1-does-not-exist"


class RepairError(RuntimeError):
    """Sanitized failure safe to include in an incident report."""


def proposal_id(proposal):
    payload = proposal.model_dump(mode="json", exclude={"id"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def propose_repair(report):
    diagnosis = validate_diagnosis(report.diagnosis, report.evidence)
    if diagnosis.status != "diagnosed":
        return None
    deployment = next(e.data["deployment"] for e in report.evidence if "deployment" in e.data)
    if (deployment["name"] != "api" or deployment["desired_replicas"] != 1
        or deployment["containers"] != [{"name": "api", "image": BAD_IMAGE, "image_pull_policy": "Never"}]):
        return None
    proposal = RepairProposal(
        id="", deployment_uid=deployment["uid"], generation=deployment["generation"],
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
        check_approval(proposal, approval)
        try:
            deployment = self.apps.read_namespaced_deployment("api", NAMESPACE, _request_timeout=TIMEOUT)
            containers = deployment.spec.template.spec.containers
            if (deployment.metadata.uid != proposal.deployment_uid
                or deployment.metadata.generation != proposal.generation
                or deployment.spec.replicas != 1 or len(containers) != 1
                or containers[0].name != "api" or containers[0].image != BAD_IMAGE
                or containers[0].image_pull_policy != "Never"):
                raise RepairError("Deployment changed since the proposal; collect a new diagnosis and approval.")
            # Atomic preconditions close the read/patch race. Only the image may change.
            body = [
                {"op": "test", "path": "/metadata/uid", "value": proposal.deployment_uid},
                {"op": "test", "path": "/metadata/resourceVersion", "value": deployment.metadata.resource_version},
                {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": BAD_IMAGE},
                {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": GOOD_IMAGE},
            ]
            check_approval(proposal, approval)
            result = self.apps.patch_namespaced_deployment("api", NAMESPACE, body, _request_timeout=TIMEOUT)
            return {"action": proposal.action, "image": GOOD_IMAGE, "deployment_uid": result.metadata.uid,
                    "generation": result.metadata.generation}
        except RepairError:
            raise
        except Exception:
            raise RepairError("Repair rejected or unavailable; no automatic retry. Inspect the deployment and credentials.") from None

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
                    or deployment["containers"] != [{"name": "api", "image": GOOD_IMAGE, "image_pull_policy": "Never"}]):
                    raise RepairError("Deployment changed during verification; recovery is not confirmed.")
                rollout = ((deployment["observed_generation"] or 0) >= deployment["generation"]
                           and all(deployment[key] == 1 for key in (
                               "replicas", "updated_replicas", "ready_replicas", "available_replicas"))
                           and len(owned) == 1 and owned[0]["phase"] == "Running"
                           and len(owned[0]["containers"]) == 1 and all(
                               c["name"] == "api" and c["image"] == GOOD_IMAGE and c["ready"]
                               and not c["waiting_reason"] for c in owned[0]["containers"]))
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
