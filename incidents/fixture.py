"""Manual fixed-fixture injection/restoration; never exposed as an agent tool."""

import argparse
import asyncio
import time

from kubernetes import client, config

from agent.diagnosis import validate_diagnosis
from agent.fixtures import (
    ACTIONS, BAD_HOST, BAD_IMAGE, BAD_MEMORY, BAD_SELECTOR, CRASH_COMMAND, GOOD_IMAGE, GOOD_MEMORY,
    GOOD_SELECTOR, fixture_matches,
)
from agent.graph import collect_evidence
from agent.kubernetes import CONTEXT, NAMESPACE, ROOT, TIMEOUT, KubernetesReader
from agent.models import Diagnosis
from agent.repair import RestrictedRepairRunner


class LocalTools:
    def __init__(self, reader):
        self.reader = reader

    async def call(self, name, arguments):
        return getattr(self.reader, name)(**arguments)


def fixed_patch(incident, container, injecting):
    """Only the one field/set of fields belonging to this incident can change."""
    base = "/spec/template/spec/containers/0"
    if incident == "bad_image":
        return [{"op": "replace", "path": base + "/image", "value": BAD_IMAGE if injecting else GOOD_IMAGE}]
    if incident == "crashloop":
        return ([{"op": "add", "path": base + "/command", "value": CRASH_COMMAND}] if injecting else
                [{"op": "remove", "path": base + "/command"}])
    if incident == "oom":
        return [{"op": "replace", "path": base + "/resources", "value": BAD_MEMORY if injecting else GOOD_MEMORY}]
    if incident == "bad_env":
        indexes = [i for i, e in enumerate(container.env or []) if e.name == "DB_HOST"]
        if len(indexes) != 1:
            raise RuntimeError("Expected exactly one non-secret DB_HOST variable")
        return [{"op": "replace", "path": base + f"/env/{indexes[0]}/value", "value": BAD_HOST if injecting else "postgres"}]
    if incident == "bad_selector":
        return [{"op": "replace", "path": "/spec/selector", "value": BAD_SELECTOR if injecting else GOOD_SELECTOR}]
    raise ValueError("Unknown incident")


def preconditions(metadata):
    return [{"op": "test", "path": "/metadata/uid", "value": metadata.uid},
            {"op": "test", "path": "/metadata/resourceVersion", "value": metadata.resource_version}]


def mutate(reader, incident, injecting):
    deployment = reader.apps.read_namespaced_deployment("api", NAMESPACE, _request_timeout=TIMEOUT)
    data = reader.get_deployment(NAMESPACE, "api")
    dep = data["deployment"]
    containers = dep["containers"]
    selector = data["service"]["selector"]
    if (dep["uid"] != deployment.metadata.uid or dep["generation"] != deployment.metadata.generation
        or dep["desired_replicas"] != 1 or len(containers) != 1
        or not any(fixture_matches(containers[0], state) for state in ("none", incident))
        or selector not in (GOOD_SELECTOR, BAD_SELECTOR if incident == "bad_selector" else GOOD_SELECTOR)):
        raise RuntimeError("Demo does not match the baseline or this single fixture; inspect it before continuing")
    already = (selector == (BAD_SELECTOR if injecting else GOOD_SELECTOR) if incident == "bad_selector"
               else fixture_matches(containers[0], incident if injecting else "none"))
    execution = {"deployment_uid": deployment.metadata.uid, "generation": deployment.metadata.generation}
    if not already:
        operations = fixed_patch(incident, deployment.spec.template.spec.containers[0], injecting)
        if incident == "bad_selector":
            service = reader.core.read_namespaced_service("api", NAMESPACE, _request_timeout=TIMEOUT)
            if service.metadata.uid != data["service"]["uid"] or service.metadata.resource_version != data["service"]["resource_version"]:
                raise RuntimeError("Service changed during fixture preparation")
            result = reader.core.patch_namespaced_service("api", NAMESPACE, preconditions(service.metadata) + operations, _request_timeout=TIMEOUT)
            execution.update(service_uid=result.metadata.uid, service_resource_version=result.metadata.resource_version)
        else:
            result = reader.apps.patch_namespaced_deployment("api", NAMESPACE, preconditions(deployment.metadata) + operations, _request_timeout=TIMEOUT)
            execution["generation"] = result.metadata.generation
    return execution


async def observe(reader, incident, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        evidence = await collect_evidence(LocalTools(reader))
        candidate = Diagnosis(status="diagnosed", incident_type=incident,
                              suspected_root_cause="Manual fixture observation; no LLM invocation.", confidence=1,
                              evidence_ids=[e.id for e in evidence if not e.error], alternative_hypotheses=[])
        diagnosis = validate_diagnosis(candidate, evidence)
        if diagnosis.status == "diagnosed":
            return
        await asyncio.sleep(2)
    raise RuntimeError(f"{incident} did not produce its required correlated symptoms within {timeout_seconds}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incident", choices=ACTIONS)
    parser.add_argument("operation", choices=["inject", "restore"])
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error("timeout must be between 1 and 300 seconds")
    configuration = client.Configuration()
    config.load_kube_config(config_file=str(ROOT / ".local" / "kubeconfig"), context=CONTEXT,
                           client_configuration=configuration, persist_config=False)
    with client.ApiClient(configuration) as api:
        reader = KubernetesReader(api)
        injecting = args.operation == "inject"
        execution = mutate(reader, args.incident, injecting)
        if injecting:
            asyncio.run(observe(reader, args.incident, args.timeout))
        else:
            RestrictedRepairRunner(api).verify(execution, timeout_seconds=args.timeout)
    print(f"{args.incident} {args.operation}: {'correlated symptoms observed' if injecting else 'rollout/endpoints/database-backed HTTP verified'}")


if __name__ == "__main__":
    main()
