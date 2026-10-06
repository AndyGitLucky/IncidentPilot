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
