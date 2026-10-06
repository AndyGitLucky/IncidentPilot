# IncidentPilot

A controlled Kubernetes incident-response project. The authoritative scope and
milestones are in [project.md](project.md); implementation choices are in
[DECISIONS.md](DECISIONS.md). See [projectlog.md](projectlog.md) for the brief
implementation history and current handoff notes.

The demo supplies a healthy **frontend → API → PostgreSQL** application and a
Prometheus scrape. M1 adds a reproducible bad-image incident with manual injection
and deterministic restoration. The agent begins in M2.

## Fresh checkout setup

Prerequisites:

- Docker Desktop running **Linux containers**, with Docker Engine 28+ / API 1.48+
  (allow at least 4 GB RAM and 2 CPUs).
- `kubectl` on PATH; Docker Desktop includes it. Use a client compatible with
  Kubernetes 1.35 (1.34–1.36).
- Windows PowerShell 5.1 or PowerShell 7, and internet access for the first build.
- Free local ports **8080** and **9090**.

From the repository root, run this single command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

On Linux/macOS with PowerShell 7, use `pwsh -NoProfile -File ./scripts/setup.ps1`.
No host Python, Helm, registry account, or manually installed kind is needed.
The script downloads kind v0.33.0 and verifies its published SHA-256 checksum,
creates the pinned single-node `incidentpilot` cluster, builds and loads the
images using a single-platform archive, applies manifests, waits for all
deployments, and runs an HTTP smoke check. It is safe to rerun: the existing
cluster and PostgreSQL PVC are reused,
and API/frontend pods restart to pick up rebuilt images.

Open [the demo](http://localhost:8080) and [Prometheus](http://localhost:9090).
Setup succeeds only after the frontend serves the database-backed message and
Prometheus reports an active scrape target with a nonzero application counter.

## Health and metrics

| URL | Meaning |
| --- | --- |
| `http://localhost:8080/` | Minimal frontend with live health and database message |
| `http://localhost:8080/healthz` | API executes `SELECT 1`; returns 503 if PostgreSQL is unavailable |
| `http://localhost:8080/api/message` | Reads seeded data from PostgreSQL |
| `http://localhost:8080/metrics` | Prometheus exposition for `demo_api_requests_total` |

The counter has `outcome="success"` and `outcome="error"` labels. Prometheus
scrapes the API every five seconds. Query
`demo_api_requests_total{job="demo-api"}` in its UI.
Readiness checks depend on the database; API and frontend liveness checks
check only their own process. Application and access logs go to container stdout.

Rerun verification without rebuilding:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

## M1: bad image tag

After setup, inject the incident:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\incidents\bad_image\inject.ps1
```

The script changes only the API container image to
`incidentpilot-api:m1-does-not-exist`. It succeeds after observing a new API pod
waiting with `ErrImageNeverPull` and a matching Warning event for that pod UID,
then prints the pod/event evidence. It waits up to 90 seconds by default; pass
`-TimeoutSeconds 120` to either incident script to adjust its Kubernetes wait.

The app images are loaded directly into kind and use `imagePullPolicy: Never`,
so this fixture requires no registry access. The failure is **ErrImageNeverPull**.
The new container never starts and has no application logs. With the normal
rolling update strategy, the old healthy API pod remains available: HTTP checks
can still pass while the rollout is stalled. Detect this case through Kubernetes
pod state, deployment image, and events.

Restore the fixed healthy image and verify recovery:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\incidents\bad_image\restore.ps1
```

Restoration sets `incidentpilot-api:m0`, waits for exactly one updated, ready,
available API replica, and runs `scripts/verify.ps1` to check the complete
frontend/API/database path and Prometheus scrape. It does not depend on rollout
history. Both scripts always use this checkout's `.local/kubeconfig`, context
`kind-incidentpilot`, and namespace `incidentpilot`.

Repeat the two commands to replay the incident. Injection and restoration are
also safe to repeat individually. If injection times out, inspect the pods and
events below, then run restoration; a failed injection does not restore itself.
If a script rejects an unexpected API image, pull policy, or replica count,
inspect the deployment or rerun setup to return to the demo fixture.

Ground truth and expected observations are in
[ground_truth.yaml](incidents/bad_image/ground_truth.yaml) and
[expected_evidence.yaml](incidents/bad_image/expected_evidence.yaml).
Historical Warning events can remain after recovery; current pod state and
rollout completion determine whether the incident is still active.

## Inspect and reset

The checkout's kubeconfig is isolated in `.local/kubeconfig`. In a new terminal:

```powershell
$env:KUBECONFIG = Join-Path (Get-Location) '.local/kubeconfig'
kubectl --context kind-incidentpilot -n incidentpilot get pods
kubectl --context kind-incidentpilot -n incidentpilot logs deployment/api
kubectl --context kind-incidentpilot -n incidentpilot get events --sort-by=.lastTimestamp
```

To deliberately reset this disposable demo, including its database data:

```powershell
& .\.tools\kind-v0.33.0.exe delete cluster --name incidentpilot
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

On Linux/macOS the downloaded kind filename has no `.exe` extension.
PostgreSQL data persists across pod restarts and setup reruns; the SQL seed runs
only on first initialization. Prometheus history is disposable and retained for
one hour. Demo credentials are public local fixture values in
`cluster/base/postgres.yaml`. Host HTTP ports bind to loopback; PostgreSQL and
the API have no host port exposure.

If setup fails, check Docker's Linux engine, port availability, and the pod
events/logs above. The first run downloads several images and can take minutes.
There is no automatic deletion of an existing cluster on failure.

If Docker reports **Virtualization support not detected**, check Task Manager →
Performance → CPU → Virtualization. For an ASUS AMD motherboard, enable
**SVM Mode** in BIOS under Advanced → CPU Configuration, then save and reboot
([ASUS instructions](https://www.asus.com/me-en/support/faq/1045141/)).
Windows also needs Virtual Machine Platform enabled for WSL 2
([Microsoft troubleshooting](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)).
Signing in to Docker does not enable firmware virtualization.

## Development checks

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```powershell
uv sync --frozen
uv run --frozen pytest
```

uv supplies Python 3.12 and installs the exact versions in `uv.lock`. The Docker
API build installs the hash-checked export `app/api/requirements.txt`. When
changing dependencies, update both:

```powershell
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file app/api/requirements.txt
```

Kustomize renders the SQL seed from `app/db/init.sql` (outside `cluster/base`):

```powershell
kubectl kustomize cluster/base --load-restrictor LoadRestrictionsNone
```

## Milestone status

- **M0 complete:** healthy app, local cluster, health checks, scraped metric,
  and reproducible setup verified.
- **M1 complete:** bad-image injection, pod/event evidence, fixed-image restore,
  and three live inject/restore cycles verified.
- Next: M2, read-only diagnosis of the bad-image incident with cited evidence.

## Validation record

2026-10-06 M0 acceptance checks on Windows, Docker Engine 29.2.0, kind v0.33.0,
and Kubernetes v1.35.8:

- `uv run --frozen pytest`: **5 passed** on Python 3.12.13; one upstream
  Starlette/AnyIO deprecation warning.
- Both PowerShell scripts parse successfully under Windows PowerShell 5.1.
- Kustomize renders 13 resources; namespace assignments and the generated SQL
  ConfigMap reference are verified.
- kind v0.33.0 downloads, matches its published SHA-256 checksum, and runs.
- A clean Git checkout of `ed9fce0`, with no existing demo cluster or local
  tools/kubeconfig, reaches a healthy state using the documented setup command.
  Docker's image/build cache was reused.
- Frontend, API, PostgreSQL, and Prometheus deployments each reach `1/1` ready.
- HTTP checks pass for the frontend, database health, seeded database message,
  and Prometheus metric exposition.
- Prometheus reports `up{job="demo-api"} = 1` and a nonzero
  `demo_api_requests_total{outcome="success",job="demo-api"}` counter.
- Repeating setup passes the smoke checks again and preserves PostgreSQL's
  PVC UID and pod UID.

The initial firmware virtualization blocker was resolved by enabling SVM in
BIOS. The Docker containerd image-import issue is handled by platform-filtered
archives in setup; see [DECISIONS.md](DECISIONS.md). Runtime verification was on
Windows/amd64; Linux/macOS and ARM hosts have not been tested.

2026-10-06 M1 acceptance on the same Windows/amd64 kind cluster:

- Three inject/restore cycles each produced an `ErrImageNeverPull` pod and
  Warning event, then restored the baseline rollout and passed all smoke checks.
- Repeated injection while broken and repeated restoration while healthy passed.
- Frontend, PostgreSQL, and Prometheus pod UIDs and the database PVC UID stayed
  unchanged during repeat validation.
- Both scripts succeeded with an unrelated `KUBECONFIG` environment variable,
  confirming they use the explicit checkout-local configuration.
- All three M1 PowerShell files parse under Windows PowerShell 5.1.
- `uv run --frozen pytest`: **5 passed**. Used `.local/uv-cache` because the
  sandbox could not write the host uv cache. The existing pytest cache also
  reported write warnings; all tests completed successfully.

The cluster was left healthy with `deployment/api` using `incidentpilot-api:m0`.
