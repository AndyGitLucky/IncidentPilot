# CrashLoopBackOff

Symptoms: Current owned API pod is waiting in CrashLoopBackOff with restart_count > 0 and last_exit_code 1. A matching Warning BackOff event and failed-pod logs name the fixed startup failure.

Checks:

- deployment startup_profile: crashloop_fixture
- pod waiting_reason: CrashLoopBackOff
- pod last_exit_code: 1
- pod restart_count: greater than zero
- events: Warning BackOff for the same pod UID
- logs: IncidentPilot crashloop fixture: startup failed for the same pod UID

Likely root cause: The API startup command exits with status 1 before serving HTTP.

Permitted repair: `restart_deployment` with the fixed parameters in this fixture's
`ground_truth.yaml`. Requires human approval of the current cited proposal.

Verification: completed baseline API rollout, exactly one owned ready pod,
Service/api selector `app=api`, ready owned endpoint, and successful
frontend/database-backed HTTP and Prometheus checks.

If observations disagree, report uncertainty and collect fresh evidence.
