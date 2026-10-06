# OOMKilled

Symptoms: Current owned API container restarts, is not ready, and reports OOMKilled in its current or last termination state. Deployment and failed pod both have the injected 16Mi request and limit.

Checks:

- deployment and pod memory request: 16Mi
- deployment and pod memory limit: 16Mi
- pod termination reason: OOMKilled
- pod restart_count: greater than zero
- pod ready: false

Likely root cause: The API container memory request and limit are reduced to 16Mi, below its startup working set.

Permitted repair: `increase_memory_limit_to_known_safe_value` with the fixed parameters in this fixture's
`ground_truth.yaml`. Requires human approval of the current cited proposal.

Verification: completed baseline API rollout, exactly one owned ready pod,
Service/api selector `app=api`, ready owned endpoint, and successful
frontend/database-backed HTTP and Prometheus checks.

If observations disagree, report uncertainty and collect fresh evidence.
