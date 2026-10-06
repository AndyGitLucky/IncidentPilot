# Project log

Brief implementation history and handoff notes. [project.md](project.md) defines
scope; [DECISIONS.md](DECISIONS.md) records rationale; [README.md](README.md)
contains setup and verification commands. Append dated entries after substantive
work, including changes, checks, related decisions, and the next unfinished step.

## 2026-10-06 — M0: healthy demo baseline

1. Read the specification and inspected the workspace. Only `project.md`
   existed; there was no Git repository, application, or cluster configuration.
2. Limited implementation to M0. Chose kind, a static nginx frontend, a Python
   3.12 FastAPI API, PostgreSQL, and a minimal Prometheus instance. See
   [M0 environment decisions](DECISIONS.md#2026-10-06-m0-local-environment).
3. Added the frontend and API. The browser checks health and displays a message
   the API reads from PostgreSQL; `app/db/init.sql` creates and seeds its table.
4. Added `/healthz` for database connectivity and `/livez` for process health.
   Readiness follows service dependencies; liveness checks avoid restarting
   healthy processes just because a dependency is unavailable.
5. Added the `demo_api_requests_total` counter with success/error labels.
   Prometheus scrapes it every five seconds. Logs use container stdout.
6. Added Dockerfiles, pinned Python dependencies and their lock/hash export,
   Kubernetes manifests, a PostgreSQL persistent volume claim, and loopback
   port mappings. The demo uses one kind node and the `incidentpilot` namespace.
7. Added `scripts/setup.ps1`: download/checksum kind, create or reuse the cluster,
   build/load images, apply manifests, wait for readiness, and run verification.
   Tools and kubeconfig stay in ignored checkout-local directories.
8. Added `scripts/verify.ps1` and five API tests. Checked script parsing,
   rendered 13 Kubernetes resources, and verified the SQL ConfigMap reference.
   All five API tests passed.
9. Initialized Git and saved the implementation baseline as `53b7345`.
10. Live setup exposed a Docker containerd image-import failure. Changed setup
    to export only the node's platform before loading the archive into kind.
    Committed as `ed9fce0`; see the
    [image-import decision](DECISIONS.md#2026-10-06-docker-containerd-image-imports).
11. Verified a clean Git checkout against an empty demo cluster. Setup downloaded
    kind, created Kubernetes, built/loaded the app, and passed HTTP and actual
    Prometheus scrape checks. Docker's existing image/build cache was reused.
12. Reran setup from the main workspace. Checks passed again; PostgreSQL kept
    the same pod and volume claim UIDs, and its seeded data remained available.
13. Marked M0 complete in the specification, decisions, and README. Committed
    the completion record as `57ac1fb`; see
    [M0 acceptance](DECISIONS.md#2026-10-06-m0-acceptance-complete).

### Handoff

- **M0 complete.** Last checked: one Ready Kubernetes v1.35.8 node; frontend,
  API, PostgreSQL, and Prometheus each have one ready replica.
- Demo: `http://localhost:8080`; Prometheus: `http://localhost:9090`.
- Main workspace kubeconfig: `.local/kubeconfig`; context: `kind-incidentpilot`.
- Verified on Windows/amd64. Other host platforms remain untested.
- Next milestone: **M1 — bad image tag**, with injection, observable failure,
  deterministic restoration, and ground truth. M1 has not started.
- AI, MCP, repair execution, Grafana, and tracing have not been implemented.

## 2026-10-06 — Progress documentation

Added this log and linked it from the session bootstrap and README so future
sessions can find the implementation history. Rechecked the live cluster: its
node is Ready and all four deployments are healthy. No runtime changes.

## 2026-10-06 — M1: reproducible bad-image incident

1. Read the specification, decisions, history, source, manifests, and tests.
   Confirmed the M0 cluster was healthy before changing the API image.
2. Added `incidents/bad_image/inject.ps1`, `restore.ps1`, their shared fixed-fixture
   helper, ground truth, and expected evidence. See the
   [M1 fixture decision](DECISIONS.md#2026-10-06-m1-bad-image-fixture).
3. Injection sets only the API image to `incidentpilot-api:m1-does-not-exist`.
   Keeping the baseline `Never` pull policy produces `ErrImageNeverPull`
   deterministically without a registry. Success requires both a current failed
   pod and its matching Warning event. The old ready API replica stays available.
4. Restoration sets `incidentpilot-api:m0`, waits for the observed deployment
   generation and one updated/ready/available replica, rejects active bad-image
   pods, and runs the existing full HTTP/database/Prometheus smoke check.
5. Ran three live inject/restore cycles; all passed. Also repeated injection
   while broken and restoration while healthy. The failed pods had distinct UIDs
   on each cycle. Non-API pods and the PostgreSQL PVC retained their UIDs during
   repeat validation. An unrelated `KUBECONFIG` did not affect either script.
6. All M1 scripts parse under Windows PowerShell 5.1; all five API tests passed.
   Redirected uv cache to `.local/uv-cache` after sandbox denial at the host
   cache. Pytest emitted existing cache write warnings and the upstream
   Starlette/AnyIO deprecation warning; tests completed successfully.
7. Documented commands, failure semantics, recovery, and limitations in README.
   Marked M1 complete. Left the cluster healthy on the baseline API image.

### Current handoff

- **M0 and M1 complete**, verified locally on Windows/amd64.
- Next unfinished acceptance criterion: **M2 — read-only agent diagnosis** for
  the bad-image case, using Kubernetes/events/logs tools and cited evidence.
- For M2, do not treat successful HTTP health as proof that a rollout is healthy.
  This fixture preserves the previous ready replica during the image failure.
- Ground truth: `incidents/bad_image/ground_truth.yaml`; evidence expectations:
  `incidents/bad_image/expected_evidence.yaml`.
- No AI, MCP, agent repair runner, additional incident type, or infrastructure
  was added.
