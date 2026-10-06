# invalid Service selector

Symptoms: The API deployment and its owned pods remain healthy. Service/api has the injected selector and no ready nonterminating endpoints; frontend API requests fail.

Checks:

- service/api selector: app=api-m4-missing
- owned API pod labels: app=api
- deployment rollout: complete
- owned API containers: ready
- owned Service EndpointSlices: no ready nonterminating endpoints

Likely root cause: Service/api selects app=api-m4-missing, which matches no healthy API pods and leaves no ready endpoints.

Permitted repair: `restore_known_service_selector` with the fixed parameters in this fixture's
`ground_truth.yaml`. Requires human approval of the current cited proposal.

Verification: completed baseline API rollout, exactly one owned ready pod,
Service/api selector `app=api`, ready owned endpoint, and successful
frontend/database-backed HTTP and Prometheus checks.

If observations disagree, report uncertainty and collect fresh evidence.
