# broken environment variable / secret

Symptoms: Current owned API pod is Running but not ready. DB_HOST is postgres-m4-missing and that pod logs Database health check failed.

Checks:

- deployment and pod db_host: postgres-m4-missing
- pod phase: Running
- pod ready: false
- logs: Database health check failed for the same pod UID

Likely root cause: The non-secret DB_HOST variable references the nonexistent postgres-m4-missing Service, preventing database connectivity.

Permitted repair: `restore_known_env_value` with the fixed parameters in this fixture's
`ground_truth.yaml`. Requires human approval of the current cited proposal.

Verification: completed baseline API rollout, exactly one owned ready pod,
Service/api selector `app=api`, ready owned endpoint, and successful
frontend/database-backed HTTP and Prometheus checks.

If observations disagree, report uncertainty and collect fresh evidence.
