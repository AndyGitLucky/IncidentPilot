"""The five fixed demo fixtures; these values never come from the model."""

GOOD_IMAGE = "incidentpilot-api:m0"
BAD_IMAGE = "incidentpilot-api:m1-does-not-exist"
BAD_HOST = "postgres-m4-missing"
BAD_SELECTOR = {"app": "api-m4-missing"}
GOOD_SELECTOR = {"app": "api"}
CRASH_COMMAND = [
    "python", "-c",
    "import sys; print('IncidentPilot crashloop fixture: startup failed', flush=True); sys.exit(1)",
]
GOOD_MEMORY = {"requests": {"cpu": "100m", "memory": "64Mi"},
               "limits": {"cpu": "1", "memory": "128Mi"}}
BAD_MEMORY = {"requests": {"cpu": "100m", "memory": "16Mi"},
              "limits": {"cpu": "1", "memory": "16Mi"}}
ACTIONS = {
    "bad_image": "rollback_image_tag",
    "crashloop": "restart_deployment",
    "oom": "increase_memory_limit_to_known_safe_value",
    "bad_env": "restore_known_env_value",
    "bad_selector": "restore_known_service_selector",
}


def container_configuration(container):
    """Only fixed non-secret host values and a classified startup command escape."""
    hosts = [e for e in container.env or [] if e.name == "DB_HOST"]
    host = hosts[0] if len(hosts) == 1 else None
    db_host = host.value if host and not host.value_from and host.value in {"postgres", BAD_HOST} else "redacted_or_missing"
    return {
        "db_host": db_host,
        "startup_profile": "crashloop_fixture" if container.command == CRASH_COMMAND and not container.args
        else "custom" if container.command or container.args else "default",
        "resources": {"requests": container.resources.requests or {}, "limits": container.resources.limits or {}}
        if container.resources else {},
    }


def fixture_matches(container, incident):
    """Require exactly one active fixture and preserve the rest of the baseline."""
    return (
        container.get("name") == "api"
        and container.get("image") == (BAD_IMAGE if incident == "bad_image" else GOOD_IMAGE)
        and container.get("image_pull_policy") == "Never"
        and container.get("db_host") == (BAD_HOST if incident == "bad_env" else "postgres")
        and container.get("startup_profile") == ("crashloop_fixture" if incident == "crashloop" else "default")
        and container.get("resources") == (BAD_MEMORY if incident == "oom" else GOOD_MEMORY)
    )
