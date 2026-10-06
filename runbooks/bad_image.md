# Bad container image tag

Symptoms: the current API image is absent from the kind node; an owned pod waits
with ErrImageNeverPull and a matching Warning names that image. Never is the
normal local image pull policy. The old healthy replica may still serve HTTP.

Checks: correlate deployment image, current owned failed pod UID, and its Warning
event. The container never starts, so logs are not required. Ignore old events.

Likely root cause: an image tag absent from the node with pull policy Never.

Permitted repair: rollback_image_tag to incidentpilot-api:m0, only after human
approval of the cited proposal. Restore the fixed baseline, not rollout history.

Verification: completed baseline rollout, exactly one owned ready API pod,
Service/api selector app=api and ready owned endpoint, and successful
frontend/database-backed HTTP and Prometheus checks.
