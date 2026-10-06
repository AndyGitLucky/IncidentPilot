# Decisions

## 2026-10-06: M0 local environment

- Use **kind** only, with Docker running Linux containers. Pin kind to v0.33.0
  and its Kubernetes v1.35.8 node image by digest. Keep kubeconfig and downloaded
  tools inside the checkout so setup does not replace a user's cluster context.
- Use PowerShell for the single setup command on the current Windows development
  machine; PowerShell 7 also permits the same script on Linux/macOS.
- The demo is a static nginx frontend, a Python 3.12 FastAPI API, and PostgreSQL.
  A database-backed message exercises all three services. PostgreSQL uses a local
  PVC; deleting the kind cluster resets its data.
- Run a minimal Prometheus instance to scrape one API request counter. Defer
  Grafana and OpenTelemetry until their milestone requires them.
- Use Kubernetes container logs as the simple log backend. A separate log store
  is unnecessary for M0; bounded Kubernetes log reads can support M2.
- Implement M0 only. Incident injection, AI, MCP, and repair remain later work.

These are choices within the specification, not MVP scope changes.

## 2026-10-06: M0 validation blocker

The development host reports `VirtualizationFirmwareEnabled=False` and Docker
Desktop cannot start its Linux engine. Keep the kind plan and complete all
available local checks. Commit the implementation baseline with this limitation
recorded. M0 remains current until fresh-cluster setup and repeatability pass;
do not begin M1 yet. Enabling BIOS virtualization requires a user-driven reboot.

## 2026-10-06: Docker containerd image imports

After enabling SVM, Docker's Linux engine and kind cluster start successfully.
The first setup run fails importing multi-platform image indexes because Docker
uses the containerd image store and downloaded only the host platform's content.
Export a single-platform archive with `docker image save --platform` and import
it with `kind load image-archive`, as documented in
[kind's known issues](https://kind.sigs.k8s.io/docs/user/known-issues/#unable-to-kind-load-docker-images).
This changes only image transport within M0. Require Docker Engine 28+ (API 1.48+)
for platform-filtered exports; do not change Docker's host-wide image store.

## 2026-10-06: M0 acceptance complete

The firmware virtualization blocker is resolved. Verified the corrected setup
from a clean checkout of `ed9fce0` with no existing kind cluster or checkout-local
tools/kubeconfig. Docker's downloaded image/build cache was reused. Setup
downloaded and checksum-verified kind, created the pinned cluster, built and
loaded images, and passed the full HTTP and Prometheus smoke check.

A second setup run from the main workspace reused the existing cluster and
preserved both the PostgreSQL PVC UID and pod UID. The database-backed response
and Prometheus scrape checks passed again. M0 is complete; M1 is the next
milestone. No incident injection or agent implementation was added.

## 2026-10-06: M1 bad-image fixture

Use checkout-local PowerShell scripts in `incidents/bad_image`, consistent with
the M0 host tooling. Inject only the API image reference:
`incidentpilot-api:m0` → `incidentpilot-api:m1-does-not-exist`. Preserve the
existing `imagePullPolicy: Never` so the missing-tag failure is reproducible
without registry credentials or network access. The expected pod/event reason
is `ErrImageNeverPull`, rather than `ImagePullBackOff`.

Preserve the normal rolling update strategy. The new pod fails to start and the
rollout stalls while the old ready API replica can still serve requests. M1
requires an observable image failure, not a forced application outage. Injection
must observe the missing-image pod state and a Warning event for that pod UID;
HTTP health alone is insufficient evidence.

Restoration sets the fixed known-good image, waits for rollout completion, and
runs the existing HTTP/database/Prometheus verification. Do not use `rollout undo`,
whose target depends on revision history. Both scripts use the checkout's
kubeconfig and explicit demo context/namespace and reject unexpected API fixture
configuration. These are manual M1 scripts; an agent and its approval/action
runner remain M2/M3 work. No MVP scope change.

## 2026-10-06: M2 diagnosis provider and read-only boundary

The user chose an LLM now, using OpenRouter, and requested a local `.env` file.
Use `OPENROUTER_API_KEY` and configurable `OPENROUTER_MODEL`, defaulting to
`openai/gpt-4.1-mini`. Its listed price is $0.40/million input and
$1.60/million output tokens; see the
[model page](https://openrouter.ai/openai/gpt-4.1-mini). Request strict
JSON-schema output and route only to compatible providers, following
[OpenRouter's structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs).
Use one bounded HTTP request per diagnosis; retain model ID and token usage.
This is a provider choice within M2, not a change to MVP scope.

Live checks of the initially considered Gemini 2.5 Flash Lite repeatedly
misdiagnosed the healthy rollout; the deterministic evidence validator rejected
those claims. Choose GPT-4.1 mini for the default based on this task-specific
evidence. Use a neutral prompt that first determines whether a current failure
exists, and include only active owned ReplicaSets in deployment observations.

Use the official MCP Python SDK's supported v1 line, pinned in `uv.lock`, and
local stdio servers. Keep `mcp/` as executable scripts without `__init__.py` so
the repository does not shadow the installed SDK's `mcp` package. LangGraph has
only `COLLECT → HYPOTHESIZE → END`. The model receives collected evidence, never
the incident library's ground truth, and has no tools or repair actions.

Read through a dedicated namespace Role and ServiceAccount, with an expiring
token in `.local/diagnosis-kubeconfig`. A separate manual setup script provisions
RBAC and credentials from the checkout's admin kubeconfig. Diagnosis cannot
provision or refresh its own credentials and cannot use admin fallback.
Project environment values and Kubernetes Secret data are excluded from tool
projections. Continue using bounded Kubernetes container logs as the simple
backend chosen in M0; add no log storage or metrics MCP until required.

Use pod-owner and deployment-owned ReplicaSet UIDs to correlate current failures,
then require the deployment image, failed container state, and Warning event to
agree before accepting the model's bad-image diagnosis. Reject invented citations,
historical failures, and healthy conclusions during stalled rollouts. Retain the
candidate diagnosis beside the validated result so rejected claims are visible.
Filter events to current API resource UIDs and send structured evidence once,
avoiding duplicated observation text in the model prompt. This keeps rollout
history from being mistaken for an active failure and reduces token cost.

The user explicitly approved sending the bounded local demo state, events, and
API logs to OpenRouter for live validation. Keep `.env` and reports untracked;
commit only `.env.example`. Repair proposals, approval gates, and execution stay
in M3.

## 2026-10-06: M3 fixed repair and human approval

Limit M3 to `bad_image`. Preserve the default M2 read-only command; opt into the
repair workflow with `--repair`. After evidence validation (`TEST`), construct
the proposal deterministically from the fixed fixture, with citations, deployment
UID/generation, a five-minute expiry, and an ID covering all proposal fields.
Use a LangGraph interrupt and an in-memory checkpoint. Only the exact typed
`approve <proposal-id>` resumes execution. A stopped process requires a new
diagnosis/proposal; saved reports cannot authorize repairs. No database is needed.

Provision a separate short-lived `repair-runner` ServiceAccount manually. Its
Role permits patching only `deployment/api`, plus the reads needed to verify
recovery. MCP servers retain exclusively read-only credentials and tools. The
runner offers only `rollback_image_tag`, restoring `incidentpilot-api:m0` from
the fixed injected image. It validates approval and live UID/generation/fixture,
then submits an image-only JSON Patch with atomic UID/resourceVersion/image tests.
Concurrent edits and reused approvals fail; writes are never retried automatically.
Kubernetes RBAC restricts the resource, while the runner restricts patch contents;
the repair token is trusted local infrastructure and never sent to the model.

Verification requires the same deployment's completed rollout, exactly one
owned ready baseline pod, and successful frontend/database-backed HTTP checks.
An old healthy replica cannot prove recovery. Record execution/verification
failures without additional remediation. Live acceptance explicitly denies one
proposal, compares resource snapshots, then requests human approval for a new
proposal. The manual M1 restore remains test cleanup. No MVP scope change.

## 2026-10-06: M4 requested before live M3 acceptance

The user explicitly requested implementation of M4. Proceed with the remaining
four fixed incidents despite the older M3 sequencing note; retain the human
approval gate and report the pending interactive live M3 acceptance separately.
Do not automatically approve live repairs during implementation or evaluation.

Use PowerShell fixtures consistent with M1: a fixed failing startup command for
`crashloop`, a 16Mi memory request/limit for `oom`, the non-secret `DB_HOST` value
`postgres-m4-missing` for `bad_env`, and Service/api selector
`app=api-m4-missing` for `bad_selector`. Only one fixture may be active at a time.
The crashloop repair restores the image's default startup command and restarts
the deployment by changing its pod template; memory repair restores the known
64Mi request and 128Mi limit. Environment and selector repairs restore fixed
known values. No arbitrary commands, environment values, or patches are accepted.

Extend the existing deployment read with API Service and owned EndpointSlice
evidence, rather than adding another MCP tool. Project termination state,
resources, fixed startup profile, and only the two known non-secret DB_HOST
values; continue excluding Secret contents and other environment values.
Add narrowly scoped Service/api patch permission to the separate repair Role.
Bind proposals to target UID/resourceVersion as well as deployment generation;
apply atomic preconditions and reject concurrent edits and reused approvals.

M4 evaluation covers all five fixtures, supported citations, proposal denial
without writes, and manual restoration with health checks. An optional human
repair mode evaluates approved live execution; offline tests exercise approval,
patches and recovery for every action. Save per-case reports and an honest
evaluation artifact. Repeated statistical benchmarks and telemetry remain M5.

Live validation found two implementation issues: the kind API emits
`endpoints:null` for an empty EndpointSlice, which the pinned SDK rejects during
model deserialization; read that bounded response with the official client's
raw-response option and project the empty list explicitly. Also replace the
older deployment-only healthy prompt rule with a combined Service/rollout check.
The initial model called ready pods healthy despite a broken selector; evidence
validation rejected it. With the corrected prompt, all five live diagnoses,
fixed proposals, denial/no-write checks and repeat inject/restore checks passed.
The user explicitly authorized bounded demo evidence export to OpenRouter for
this M4 evaluation. Raw evidence remains ignored under `.local`; commit only the
non-sensitive result summary. Agent repair success stays unset in this run,
because recovery used manual fixture restoration rather than approved execution.

## 2026-10-06: M3 acceptance snapshot comparison regression

The user's live run denied one proposal without writes, then approved proposal
`f02bc67cb6213bdd`. The separate repair runner restored the known-good image,
advanced the API generation from 46 to 47, and verified completed rollout and
database-backed HTTP recovery. Its final unrelated-resource assertion failed.

Reproduced the cause under Windows PowerShell 5.1: piping a JSON array directly
from `ConvertFrom-Json` into `Where-Object` passes the entire array as one object.
Member enumeration made the filters retain that array, including the expected
API deployment and failed pod changes. Assign the decoded array first, then
enumerate/filter the resources in a separate pipeline. Continue checking every
non-API workload, Service (including Service/api), pod and PVC identity/spec.
Extract the comparison into a function and test its actual AST body against
expected API recovery and unrelated resource edits/replacements/removals/additions.

Save full before/after snapshots under ignored `.local/m3` for future checks.
The original run did not persist those snapshots, so its historical full-spec
comparison cannot be replayed. Saved report validation and current read-only
verification confirm the approved live repair and unchanged unrelated pod UIDs.
Record that evidence separately from a clean exit of the corrected acceptance
script, which still requires a new live run and a fresh human-approved proposal.
Do not reuse the already consumed approval or auto-approve another repair.
