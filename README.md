# IncidentPilot

A controlled Kubernetes incident-response project. The authoritative scope and
milestones are in [project.md](project.md); implementation choices are in
[DECISIONS.md](DECISIONS.md). See [projectlog.md](projectlog.md) for the brief
implementation history and current handoff notes.

The demo supplies a healthy **frontend → API → PostgreSQL** application and a
Prometheus scrape. M1 adds a reproducible bad-image incident with manual injection
and deterministic restoration. M2 adds read-only MCP evidence collection and a
LangGraph diagnosis using an OpenRouter LLM. M3 adds a fixed-image repair proposal,
an explicit human approval gate, and automatic recovery verification.
M4 extends that workflow to all five fixed incident classes, with replayable
fixtures, runbooks, evidence checks, and a per-case evaluation artifact.
M5 adds repeated benchmarks, three diagnosis comparison modes, metrics tables,
OpenTelemetry traces, and one local Grafana dashboard.

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

## M2: read-only LLM diagnosis

After the demo setup, install the agent and provision its read-only credentials:

```powershell
uv sync --frozen --extra agent
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-diagnosis.ps1
```

Copy `.env.example` to `.env` if `.env` does not exist. Paste your OpenRouter key
into `OPENROUTER_API_KEY`. The agent loads this file directly; VS Code terminal
environment injection is optional. Existing process environment variables take
precedence. `.env` is ignored by Git and excluded from Docker builds.

The default is `openai/gpt-4.1-mini`: a low-cost model with structured outputs,
listed at $0.40/million input tokens and $1.60/million output tokens on 2026-10-06.
See [OpenRouter's model page](https://openrouter.ai/openai/gpt-4.1-mini).
Change `OPENROUTER_MODEL` in `.env` to use another model supporting JSON-schema
responses. Provider routing requires support for all requested parameters.

Requests identify the app as **IncidentPilot** in OpenRouter using
`HTTP-Referer: https://local.incidentpilot/`, `X-OpenRouter-Title: IncidentPilot`,
and the compatible `X-Title: IncidentPilot` header. Set `OPENROUTER_APP_URL` to
override the identifier. This URL is attribution metadata; the demo still runs
at `http://localhost:8080` and no DNS/HTTPS setup is needed. A public app entry
is requested, matching the user's working Bewerbung Pipeline app. The previous
generic localhost identifier sent `X-OpenRouter-App-Visibility: hidden`, which
disables public app pages and cannot be reversed by changing later request
headers. The distinct named identifier avoids reusing that hidden entry; see
[OpenRouter's attribution guide](https://openrouter.ai/docs/app-attribution).
Verified 2026-10-06 with one synthetic `Reply exactly OK.` request: the new
[IncidentPilot app page](https://openrouter.ai/apps?url=https%3A%2F%2Flocal.incidentpilot%2F)
returns HTTP 200 and title `IncidentPilot | OpenRouter`. Earlier requests keep
their previous attribution; the new identifier applies to subsequent requests.

Inspect the exact evidence locally, without making an LLM request:

```powershell
uv run --frozen --extra agent python -m agent --collect-only --output .local/evidence.json
```

Run diagnosis in either a healthy or injected state:

```powershell
uv run --frozen --extra agent python -m agent --output .local/diagnosis.json
```

Diagnosis sends the demo pod/deployment state, current API resource events,
and bounded API container logs to OpenRouter. M4 also includes Service/EndpointSlice
routing, memory resources, termination state, and a classified startup profile.
Only the known non-secret DB_HOST values `postgres` and `postgres-m4-missing`
are projected; other environment values, Secret references/contents, kubeconfig
credentials, arbitrary startup commands, and API keys are excluded.
Each run makes one model request with a 2,048-token output limit and no automatic
retries. Collection alone uses no API credits.

The JSON report contains `COLLECT → HYPOTHESIZE`, timestamped evidence with IDs,
the model's candidate diagnosis, the validated diagnosis, model ID, and token
usage. Each citation resolves to an evidence entry in the same report. An active
bad-image diagnosis requires the current deployment image, an owned pod waiting
with `ErrImageNeverPull`, and a matching Warning event for that pod UID and image.
Historical pod events are excluded; old ReplicaSets alone do not prove failure.
The failed container's logs explicitly say `not_started`; the previous healthy
replica's logs remain separate. Missing evidence, unsupported model claims,
provider errors, and incomplete model output produce `inconclusive` and exit 2.
A supported diagnosis or healthy result exits 0.

Two local stdio MCP servers expose only `get_pods`, `describe_pod`, `get_events`,
`get_deployment`, and `search_logs`. Pod/ReplicaSet reads cap at 100 resources;
events paginate up to 500. Incomplete reads fail explicitly. Logs cap at 10 pods,
100 lines and 8 KB per container, from the last 10 minutes during diagnosis.
The agent uses `.local/diagnosis-kubeconfig` and explicit `kind-incidentpilot`
context, never the admin kubeconfig or ambient `KUBECONFIG`. Its Kubernetes
ServiceAccount can read only demo pods, logs, events, deployments, ReplicaSets,
Services, and EndpointSlices.
It cannot patch workloads, read Secrets, execute commands, or delete volumes.
The token expires after one hour; rerun `setup-diagnosis.ps1` to refresh it.
There is no repair tool or execution stage in M2.

For the live acceptance sequence, which calls OpenRouter three times and uses
the manual M1 scripts to inject and restore the fixture:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-diagnosis.ps1
```

It checks healthy → bad image → healthy diagnoses, evidence citations, read-only
permissions, and unchanged resource identities/specifications during each
diagnosis. Restoration runs in `finally` after attempted injection. Reports go
to `.local/m2/healthy.json`, `injected.json`, and `restored.json`.

## M3: human-approved bad-image repair

Provision separate repair credentials after the M2 setup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-repair.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\incidents\bad_image\inject.ps1
uv run --frozen --extra agent python -m agent --repair --output .local/repair.json
```

The workflow runs `COLLECT → HYPOTHESIZE → TEST → PROPOSE_FIX`, prints the
evidence-backed proposal, and pauses at `WAIT_FOR_APPROVAL`. Read its fixed action,
parameters, citations, target UID/generation, and expiry. Type the exact
`approve <proposal-id>` phrase shown to run `EXECUTE → VERIFY → REPORT`.
Anything else, including EOF, denies execution. Each proposal expires after five
minutes. The report is saved before the prompt and updated after the decision.
The default command without `--repair` remains read-only.

For the bad-image fixture, the permitted action is `rollback_image_tag`: restore `deployment/api`'s
`api` container from `incidentpilot-api:m1-does-not-exist` to
`incidentpilot-api:m0`. Parameters are fixed by code rather than model output.
The runner rechecks the live target and uses atomic JSON Patch preconditions;
concurrent deployment edits, replacement targets, expired approval, and attempts
to reuse approval reject execution. Start a new run to get a fresh proposal.
An interrupted process cannot resume from a saved JSON report.

MCP still uses `.local/diagnosis-kubeconfig`; only the approved runner opens
`.local/repair-kubeconfig`. Neither uses admin credentials or ambient kubeconfig.
The repair Role grants patch only on `deployment/api` and (since M4) `service/api`,
plus limited verification reads. It denies other deployments/Services, Secrets,
exec, and deletion. RBAC restricts the target resources; the trusted runner
enforces each action's fixed patch contents. Refresh its one-hour token with
`setup-repair.ps1`.

Recovery requires exactly one updated/ready/available baseline API replica, an
owned running baseline pod, and successful frontend, database health, and seeded
database-message HTTP checks. The previous ready replica cannot establish rollout
recovery. Reports include approval, execution, verification, and an explicit
outcome. Failures exit 2 and do not trigger further repair. Healthy runs and denied
proposals exit 0; denial records `denied`, never `recovered`.

Run live acceptance (two OpenRouter calls, with human approval on the second):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-repair.ps1
```

The script checks both Roles, injects the existing fixture, denies one proposal,
and compares workload/Service/volume identities and specifications to prove no
write occurred. It then displays a fresh proposal for human approval and requires
verified recovery. It also checks unrelated resources are unchanged and reruns
the HTTP/Prometheus smoke check. Reports are `.local/m3/denied.json` and
`approved.json`. Manual fixture cleanup runs in `finally` even if approval is
denied or execution fails; this cleanup is separate from the agent workflow.
The script also saves `before.json` and `after.json` resource snapshots so a
failed unrelated-resource comparison can be inspected without replaying a repair.

## M4: five incident types

Install the agent extra and refresh both Roles/tokens after updating the checkout:

```powershell
uv sync --frozen --extra agent
.\scripts\setup-diagnosis.ps1
.\scripts\setup-repair.ps1
```

| Fixture | Injection | Required symptoms | Fixed approved action |
| --- | --- | --- | --- |
| `bad_image` | Missing local image tag | Owned failed pod and matching `ErrImageNeverPull` Warning | `rollback_image_tag` |
| `crashloop` | Startup command exits 1 | `CrashLoopBackOff`, restarts, matching BackOff Warning and failed-pod logs | `restart_deployment` |
| `oom` | Memory request/limit reduced to 16Mi | `OOMKilled` termination and restarts in a non-ready owned pod | `increase_memory_limit_to_known_safe_value` |
| `bad_env` | `DB_HOST=postgres-m4-missing` | Running non-ready owned pod and database-failure logs | `restore_known_env_value` |
| `bad_selector` | Service selector `app=api-m4-missing` | Healthy baseline rollout/pods, no selected pods or ready endpoints | `restore_known_service_selector` |

Each directory under `incidents/` contains `inject.ps1`, `restore.ps1`, ground
truth, and expected evidence. Five short [runbooks](runbooks/) document checks,
permitted actions, and verification; model retrieval remains a later comparison.
The evaluator keeps ground truth out of the LLM prompt.

For example, replay the OOM fixture manually:

```powershell
.\incidents\oom\inject.ps1
uv run --frozen --extra agent python -m agent --repair --output .local/oom-repair.json
.\incidents\oom\restore.ps1
```

Use the corresponding directory for the other incidents. Only one fixture may
be active at a time; the scripts reject mixed or foreign configuration. Repeated
injection and restoration are safe. A failed injection requires manual cleanup
with that fixture's restore script. They always use this checkout's demo context.
The four new scripts require uv and the agent extra; default timeout is 120s,
adjustable with `-TimeoutSeconds`.

Crashloop repair removes only the fixed injected startup command, which restarts
the pod template using the image's normal entrypoint. OOM repair restores the
known 64Mi memory request and 128Mi limit. Environment repair changes only the
non-secret DB_HOST value. Selector repair changes only Service/api's selector,
bound to its UID/resourceVersion. All actions require the exact current human
approval phrase, recheck their fixed fixture, use atomic JSON Patch preconditions,
and verify baseline rollout, owned ready endpoints, and database-backed HTTP.

Run the live M4 evaluation (five OpenRouter requests, no agent writes):

```powershell
.\scripts\verify-incidents.ps1
```

It injects every fixture, checks repeat injection, validates the model diagnosis
and proposal, denies the proposal, proves workload/Service/volume specs and
unrelated pod identities did not change, manually restores twice, and runs the
HTTP/database/Prometheus smoke check. Cleanup runs even if diagnosis fails.
Reports and `.local/m4/results.json` record per-case success, timing, token usage,
and failures. A failed case exits nonzero; cleanup failure stops further injection.
Manual restoration is never counted as agent repair success.

For local fixture/MCP validation without a provider request:

```powershell
.\scripts\verify-incidents.ps1 -FixturesOnly
```

That mode checks symptoms and recovery and leaves model accuracy unset. To also
evaluate approved live agent execution, use `-HumanRepair`; it denies the first
proposal, displays a fresh proposal, and requires human input for each incident.
There is no automatic approval option. M4's single-pass evaluation does not
replace M5's repeated benchmark, comparisons, traces, or dashboard.

## M5 benchmark and observability

Run the complete repeated benchmark with one script, after demo setup and
refreshing the read-only credentials:

```powershell
uv sync --frozen --extra agent
.\scripts\setup-diagnosis.ps1
.\scripts\benchmark.ps1 -Repetitions 2
```

The live command sends bounded demo evidence to OpenRouter and incurs provider
usage. Two repetitions make 33 requests: three healthy controls plus three
comparisons for each of ten injected episodes. Every episode captures one MCP
snapshot and replays it identically to A (`raw`, generic prompt and flattened
observations), B (`structured`, the existing evidence-aware workflow), and C
(`runbook`, B plus keyword-ranked retrieval of two of the five runbooks). The
comparison order rotates between repetitions. Retrieval receives observations,
never the injected label or ground-truth files. This measures prompt/evidence
representation and retrieval, rather than dynamic model tool selection.

The default denies all proposals and restores each fixed fixture manually. To
measure approved live repairs, refresh the separate runner credentials with
`scripts/setup-repair.ps1`, then add `-HumanRepair`. After the three comparisons,
each episode collects fresh evidence for a new structured proposal and asks for
the exact `approve <id>` input. Its extra request/token usage is saved separately.
Denial, expiry, or a stopped process never authorizes a write. Run benchmarks
alone: concurrent setup, injection, or repair invalidates preservation checks.

For a reproducible local check that makes **no provider requests**:

```powershell
.\scripts\benchmark.ps1 -Repetitions 2 -FixturesOnly
```

Each invocation creates a separate `.local/m5/<UTC timestamp>` directory. Use
`-Output <new-directory>` for a fixed destination; an existing result is never
overwritten. The directory contains:

- `results.json`: per-episode outcomes, comparison scores, aggregate metrics,
  denominators, timing, trace IDs, and limitations. It is updated after each
  comparison/cleanup so partial failures remain inspectable.
- `metrics.md`: the comparison metrics table; `N/A` means not measured.
- `metrics.prom`: Prometheus exposition for the current run.
- `traces.jsonl`: OpenTelemetry span IDs, parent relationships, names, timing,
  and sanitized attributes. Logs, evidence payloads, credentials, and exception
  bodies are excluded from spans.
- Per-episode evidence/diagnosis/proposal reports under `00-none`, `01-bad_image`,
  etc. These stay ignored; only non-sensitive summaries belong in Git.

Accuracy uses all attempted incident comparisons, including failed requests.
Candidate accuracy and validated accuracy are separate. Root-cause scoring is
incident-type label matching. Top-2 recall uses the primary label and the first
explicit alternative incident key. Evidence completeness is coverage of cited
required blocks (deployment, failed pod, matching events/logs as applicable);
the existing validator separately checks current resource correlation. A wrong
or unsupported model answer is a completed measurement; infrastructure failures,
unapproved writes, cleanup failures, or requested telemetry export failures make
the script exit nonzero. Cleanup failure stops further injection.

MTTD measures injection to observed fixture symptoms. MTTDg measures symptom
detection to a supported diagnosis, including waiting behind earlier comparisons.
Comparison latency is also reported separately. Agent MTTR measures injection to
verified approved recovery, including human waiting; manual cleanup time is a
separate metric. Repair success uses only approved attempts, regression uses
immediate independent post-repair checks, and unnecessary proposal rate uses the
healthy controls. Denied/manual-only runs leave agent repair/MTTR/regression
unset. Missing provider usage is missing data, with token coverage reported.
Percentiles use nearest rank; two runs per fixture are descriptive and do not
establish statistical reliability. Physical snapshot reads and logical replay
reads have separate counts.

To see the benchmark in the local observability stack:

```powershell
.\scripts\setup-observability.ps1
.\scripts\benchmark.ps1 -Repetitions 2 -FixturesOnly -Observability
```

Open [the IncidentPilot dashboard](http://127.0.0.1:13000/d/incidentpilot/incidentpilot).
It shows the active fixture, live API scrape health and error rate, latest API pod
snapshot, diagnosis stage, approved-agent MTTR, manual cleanup duration, latest
actions, tool/token counts, and the latest trace ID. Copy a trace ID into Grafana
**Explore → Tempo → Trace ID** to view its hierarchy. Fixture-only traces include
injection, symptom detection, reads, and cleanup; live comparisons also trace
graph diagnosis, proposal, approval, and approved execution/verification stages.

Setup provisions pinned Grafana, single-binary Tempo, and Pushgateway in the
separate `incidentpilot-observability` namespace. It updates the existing
Prometheus scrape configuration before a benchmark. Loopback-only port forwards
are recorded under `.local/observability`: Grafana `13000`, Tempo OTLP `14318`,
Tempo query API `13200`, and Pushgateway `19091`. Rerun setup if a forward stops.
Viewer access is anonymous for this local demo. Grafana's public local admin
fixture password is `incidentpilot-local-demo`; all stack storage is disposable,
and trace/Prometheus retention is one hour. No external telemetry is configured.
Benchmark pushes replace one fixed Pushgateway job, so the dashboard describes
the latest run; old series are not accumulated per run.

The implementation follows the official [OpenTelemetry exporter setup](https://opentelemetry.io/docs/languages/python/exporters/),
[Tempo local deployment](https://grafana.com/docs/tempo/latest/set-up-for-tracing/setup-tempo/deploy/locally/linux/),
and [Prometheus batch-job Pushgateway guidance](https://prometheus.github.io/client_python/exporting/pushgateway/).

2026-10-06 provider-free M5 results:

| Metric | Result |
|---|---:|
| Repetitions per incident | 2 |
| Correlated incident symptoms and verified manual recovery | 10/10 |
| Healthy controls | 1/1 |
| Read-only collection and unrelated-resource preservation | 11/11 |
| Mean time to detect fixture symptoms | 3.720 s |
| Mean manual cleanup duration | 8.355 s |
| P50 / P95 incident duration (including cleanup) | 11.610 / 15.813 s |
| Mean shared MCP calls per episode, including control | 5.727 |
| Telemetry export errors | 0 |
| Live A/B/C model scores, tokens, approved repair success / MTTR | Not measured |

The [M5 result artifact](eval/results/m5-2026-10-06.json) contains the individual
episodes and metric definitions. **229 tests passed**, including offline mocked
provider comparisons for all five incidents in all three modes, missing-token
handling, exact approval/fresh evidence, regression checks, failure artifacts,
trace privacy/parentage, and stopping after cleanup failure. PowerShell parsing,
manifest rendering, dependency-lock checks, and `git diff --check` pass.
Prometheus scraped `incident_total = 10`; both Grafana data-source proxies returned
real metrics/stored Tempo traces. All demo and observability deployments are ready.

Automatic approval review rejected the live A/B/C benchmark because it exports
Kubernetes incident evidence to OpenRouter and this request did not explicitly
authorize that payload/destination. No live model request was made in M5. Obtain
explicit approval before running/publishing that comparison; do not interpret
fixture checks or mock provider tests as model accuracy. Approved live repair
metrics additionally require interactive `-HumanRepair` decisions.

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
uv sync --frozen --extra agent
uv run --frozen --extra agent pytest
```

uv supplies Python 3.12 and installs the exact versions in `uv.lock`. The Docker
API build installs the hash-checked export `app/api/requirements.txt`. When
changing dependencies, update both:

```powershell
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file app/api/requirements.txt
```

Agent dependencies are an optional extra; the demo API image keeps its small
runtime dependency set. Without the extra, agent test modules are skipped.

Kustomize renders the SQL seed from `app/db/init.sql` (outside `cluster/base`):

```powershell
kubectl kustomize cluster/base --load-restrictor LoadRestrictionsNone
```

## Milestone status

- **M0 complete:** healthy app, local cluster, health checks, scraped metric,
  and reproducible setup verified.
- **M1 complete:** bad-image injection, pod/event evidence, fixed-image restore,
  and three live inject/restore cycles verified.
- **M2 complete:** read-only Kubernetes/logs MCP, LangGraph evidence collection,
  and a live OpenRouter diagnosis with validated citations.
- **M3 complete:** cited fixed-image proposal, denied execution without writes,
  human-approved restricted live repair, recovery and unchanged unrelated
  resources verified by the corrected acceptance script.
- **M4 complete:** all five fixtures, evidence validation, fixed repair proposals,
  runbooks, and live diagnosis/denial/manual recovery evaluation passed.
- **M5 implemented:** repeated benchmark, A/B/C comparison harness, metrics
  table, local OpenTelemetry/Tempo traces, and one Grafana dashboard. Repeated
  provider-free acceptance passes; live model comparison needs evidence-export
  approval. See the M5 validation record and `scripts/benchmark.ps1`.

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

2026-10-06 M2 acceptance on the same Windows/amd64 kind cluster:

- `uv run --frozen --extra agent pytest`: **40 passed**, with the existing
  upstream Starlette/AnyIO deprecation warning.
- Official MCP protocol tests confirm exactly four Kubernetes read tools and
  one bounded logs tool, structured results, and rejection of unknown tools.
- Both M2 PowerShell scripts parse under Windows PowerShell 5.1.
- The reader can inspect pods, logs, events, and deployments; RBAC denies
  deployment patching, pod exec, Secret reads, and PVC deletion.
- Live `verify-diagnosis.ps1` passed with `openai/gpt-4.1-mini`: healthy
  `no_active_incident` (6,813 tokens), injected `bad_image` (7,987 tokens), and
  restored `no_active_incident` (6,869 tokens). The injected diagnosis cited
  `e1` deployment, `e3` matching events, and `e5` failed-pod description, plus
  the pod snapshot `e2`.
- Resource identities, generations, and specifications did not change during
  any diagnosis. Manual M1 restoration and HTTP/database/Prometheus checks
  succeeded; the demo was left healthy.
- The agent's dependencies remain outside the demo API image. The root-only
  hash export `app/api/requirements.txt` is unchanged.

The initially considered Gemini 2.5 Flash Lite produced unsupported healthy-state
claims; evidence validation rejected them. GPT-4.1 mini passed the acceptance
sequence. This is a single live M2 acceptance sequence, not the M5 benchmark.

2026-10-06 M3 implementation checks:

- `uv run --frozen --extra agent pytest`: **73 passed**, with the existing
  upstream Starlette/AnyIO warning. Tests cover denial, mismatched/expired/altered
  approval, stale deployments, atomic patch contents/conflicts, absence of writes
  before approval, repeated resume, and rollout/database verification failures.
- All PowerShell scripts parse under Windows PowerShell 5.1; `git diff --check`
  passes. No new dependencies were added.
- Live RBAC checks pass: the reader cannot patch API; the runner can patch API,
  but cannot patch frontend or Services, read Secrets, exec, or delete PVCs.
- The new verifier passes against the healthy live cluster with the separate
  repair credentials, confirming rollout, frontend, and database-backed responses.
- Full `verify-repair.ps1` acceptance remains pending. The first OpenRouter
  connection failed; test cleanup restored rollout/HTTP/Prometheus health.
  Automatic approval review rejected a network-enabled retry because it requires
  explicit authorization to export bounded demo evidence to OpenRouter.

2026-10-06 M4 acceptance on the same Windows/amd64 kind cluster:

- **187 tests passed** with one existing upstream Starlette/AnyIO warning.
  Tests cover all five diagnosis classes, current UID/configuration/citation
  correlation, rejection of mixed or substituted failures, exact approval and
  dispatch, fixed patch contents, concurrent edits, reused/tampered/expired
  proposals, endpoint ownership, and rollout/Service/database verification.
- All PowerShell scripts parse; CLI help, manifest rendering, and diff checks pass.
- Live RBAC checks confirm the reader cannot write or read Secrets. The runner
  can patch only Deployment/api and Service/api; other workloads, Secrets, exec,
  and PVC deletion remain denied.
- Live evaluation with `openai/gpt-4.1-mini`: **5/5 supported diagnoses and
  correct fixed repair proposals**. Every denial caused no writes; repeated
  injection and manual restoration passed all health checks and preserved
  unrelated workload/volume identities. A final healthy run returned
  `no_active_incident`, with no proposal or approval request.
- The provider-free fixture/MCP mode also passed all five cases, including
  repeatability, unrelated-resource preservation and automatic recovery checks.
- Non-sensitive summary: [M4 result artifact](eval/results/m4-2026-10-06.json).
  Full per-case evidence stays in ignored `.local/m4`. These are single-pass
  fixture/type-label results, not statistical benchmark claims.
- Recovery in this live run used manual fixture restoration. Live approved agent
  execution remains the explicit interactive `-HumanRepair` check; agent repair
  success is deliberately unset in the summary. All approved paths pass offline
  graph/runner tests. The cluster was left healthy.

2026-10-06 M3 approved live repair and acceptance-check fix:

- The user denied one cited proposal, then approved the fixed-image repair.
  The agent restored API generation 46 → 47 and verified one ready baseline
  replica, owned ready endpoints, frontend and database-backed HTTP recovery.
  Saved report validation and a fresh read-only health check confirmed recovery.
- The final Windows PowerShell unrelated-resource check incorrectly retained
  the entire decoded JSON array, including the repaired API target. Assigned
  the array before filtering and added a regression test using the actual
  PowerShell function body. Its 19 scenarios accept API recovery and reject
  unrelated Deployment/Service/pod/PVC edits, replacement, removal or addition.
- Full before/after snapshots are now saved. The original run's full spec
  comparison cannot be replayed; current unrelated pod UIDs match its evidence.
  [M3 repair record](eval/results/m3-2026-10-06.json) distinguishes successful
  approved recovery from the pending clean corrected-script acceptance run.
- All 188 tests pass. The cluster remains healthy; the fix performs no repair
  and does not reuse the consumed approval. To get a clean full-script result,
  rerun `scripts/verify-repair.ps1` and approve its fresh proposal.

2026-10-06 M3 full acceptance completed:

- The user reran the corrected script and approved proposal `dc5970d216d3eef1`.
  The script printed `M3 acceptance passed`; API generation advanced 48 → 49,
  the known-good image was restored and automatic rollout/endpoints/HTTP/database
  verification passed. Full HTTP/Prometheus verification and cleanup also passed.
- Revalidated `.local/m3/denied.json` and `approved.json`, then compared the saved
  `before.json`/`after.json` using the actual PowerShell comparison function and
  an independent parsed-object check. Unrelated workload/Service/pod/PVC identities
  and specs match. A fresh read-only runner verification confirms live health.
- Updated the [M3 result artifact](eval/results/m3-2026-10-06.json) to record full
  acceptance success. M0–M4 are complete; M5 is next. No new incident or repair
  was initiated while recording this result.
