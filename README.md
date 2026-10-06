# IncidentPilot

A controlled Kubernetes incident-response project. The authoritative scope and
milestones are in [project.md](project.md); implementation choices are in
[DECISIONS.md](DECISIONS.md).

The M0 baseline supplies a healthy **frontend → API → PostgreSQL** application
and a Prometheus scrape. Incident injection and the agent begin in later milestones.

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
images using a single-platform archive, applies manifests, waits for all deployments, and runs an HTTP smoke
check. It is safe to rerun: the existing cluster and PostgreSQL PVC are reused,
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

- M0: application and setup implemented; cluster acceptance remains unverified.
- Next unfinished acceptance criterion: run setup on a host with working Docker,
  confirm a healthy fresh cluster, and rerun setup to confirm repeatability.
- After M0 acceptance: M1, reproducible bad-image injection and restoration.

## Validation record

2026-10-06 baseline checks:

- `uv run --frozen pytest`: **5 passed** on Python 3.12.13; one upstream
  Starlette/AnyIO deprecation warning.
- Both PowerShell scripts parse successfully under Windows PowerShell 5.1.
- Kustomize renders 13 resources; namespace assignments and the generated SQL
  ConfigMap reference are verified.
- kind v0.33.0 downloads, matches its published SHA-256 checksum, and runs.
- Setup stops at its Docker preflight: Docker Desktop's Linux engine returns
  HTTP 500, and Windows reports firmware virtualization disabled.

Container builds, a healthy cluster, HTTP smoke verification, and setup reruns
could not be tested on this host. M0 acceptance is pending those checks; this
commit provides the implementation baseline, not evidence of cluster recovery.
