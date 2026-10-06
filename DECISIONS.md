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
