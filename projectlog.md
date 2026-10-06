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

## 2026-10-06 — Commit workflow preference

Recorded the user's standing instruction in the `project.md` session bootstrap
and new-session prompt: automatically commit each successful task after relevant
checks and diff review, unless the user requests uncommitted changes. Commit only
task-related changes and report the commit hash and validation results.
Documentation-only change; verified the diff with `git diff --check`.

## 2026-10-06 — M2: read-only LLM diagnosis

1. Read the specification, decisions, history, manifests, application, tests,
   and incident fixture. Confirmed the cluster was healthy and scoped work to M2.
2. The user chose OpenRouter and requested `.env` for the API key. Created the
   ignored local file and tracked `.env.example`; direct dotenv loading avoids
   reliance on VS Code terminal environment injection. The key was supplied
   locally and never printed or committed.
3. Added the official MCP SDK stdio servers in `mcp/`: four Kubernetes read
   tools and one bounded Kubernetes-log tool. Added projections excluding
   environment/Secret data and UID-based deployment/ReplicaSet/pod correlation.
4. Added a namespace Role, ServiceAccount, and RoleBinding, plus manual
   `setup-diagnosis.ps1` to mint a one-hour read-only kubeconfig. Diagnosis cannot
   fall back to admin credentials or refresh its own token. Verified RBAC allows
   reads and denies workload mutation, exec, Secret reads, and PVC deletion.
5. Added Pydantic evidence/diagnosis/report models and LangGraph
   `COLLECT → HYPOTHESIZE → END`. The LLM receives collected evidence through
   OpenRouter's structured-output API. It sees no ground truth and has no repair
   tool. Evidence validation rejects unsupported diagnoses or healthy claims,
   invented citations, wrong pod UIDs, and historical failures. Reports retain
   the candidate and validated diagnosis, model ID, and token usage.
6. Initial live Gemini 2.5 Flash Lite checks misdiagnosed the healthy state;
   validation rejected those claims. Scoped events to current API resource UIDs,
   removed duplicated prompt evidence, used a neutral prompt, and selected
   GPT-4.1 mini as the stronger low-cost default. See the M2 decision record.
7. Added `--collect-only` for local inspection without external requests.
   Automatic approval review initially rejected sending local evidence to
   OpenRouter; the user then explicitly approved the bounded demo state, events,
   and API logs. Subsequent live validation used that approval.
8. All 40 tests passed, including MCP protocol, evidence/citation validation,
   provider failures, environment loading, bounds, and data projections. Both
   new PowerShell scripts parse under Windows PowerShell 5.1. The existing
   upstream Starlette/AnyIO warning remains.
9. `verify-diagnosis.ps1` passed the live healthy → injected → restored sequence
   with GPT-4.1 mini. The injected diagnosis cited the current missing image,
   owned failed pod, and Warning event. Resource UIDs, generations, and specs
   stayed unchanged during each diagnosis. Manual restoration in `finally`
   passed rollout and HTTP/database/Prometheus verification.
10. Updated README, decisions, and milestone status. Kept agent dependencies in
    an optional extra; re-exported the demo API requirements and confirmed no
    change to its runtime dependency set. Reviewed the task diff before commit.

### Current handoff

- **M0, M1, and M2 complete**, verified on Windows/amd64.
- The cluster is healthy on `incidentpilot-api:m0`.
- Diagnosis: `uv run --frozen --extra agent python -m agent`.
- Model: `openai/gpt-4.1-mini` through OpenRouter. `.env` is local and ignored.
- Refresh the one-hour reader token with `scripts/setup-diagnosis.ps1`.
- Live reports: `.local/m2/healthy.json`, `injected.json`, `restored.json`.
- Next: **M3 — human-approved repair**, still limited to the bad-image case.
  No repair proposal, approval gate, or agent action runner exists yet.

## 2026-10-06 — OpenRouter app attribution

Fixed requests appearing as an unknown app by adding the required
`HTTP-Referer: http://localhost:8080` alongside the current
`X-OpenRouter-Title: IncidentPilot` header. The previous title-only header could
not establish attribution. Set new app visibility to hidden so attribution is
available in account analytics without creating a public listing. Documented
the behavior in README and checked the emitted HTTP headers through the existing
mock transport tests. All 40 tests passed; no paid live request was needed.
M3 remains the next milestone.

## 2026-10-06 — M3: human-approved repair implementation

1. Read project scope, decisions, history, manifests, code, and tests; confirmed
   the existing kind demo was healthy and limited work to `bad_image`.
2. Preserved the M2 read-only command. Added `--repair` and LangGraph
   `TEST → PROPOSE_FIX → WAIT_FOR_APPROVAL → EXECUTE → VERIFY → REPORT`.
   The gate uses an in-memory checkpoint and requires the exact approval phrase
   for a concrete proposal. EOF/denial cannot reach the runner; saved JSON cannot
   resume execution. Proposals bind citations, fixed parameters, UID/generation,
   and a five-minute expiry. See the M3 decision record.
3. Added separate repair RBAC and manual one-hour token provisioning. MCP retains
   its read-only credentials. The runner can only restore the known baseline API
   image, checks current fixture/UID/generation, and atomically tests UID/resource
   version/image before its image-only patch. No automatic write retry exists.
4. Added recovery verification of the exact completed rollout, owned ready
   baseline pod, frontend, database health, and seeded database-backed response.
   Execution/verification failures are reported without additional repair.
5. Added a live acceptance script: deny a cited proposal and compare snapshots,
   request human approval of a fresh proposal, verify recovery and unchanged
   unrelated resources, and run the full HTTP/Prometheus smoke check. Manual M1
   cleanup runs even on failure.
6. All 73 tests pass with the existing upstream Starlette/AnyIO warning. All
   PowerShell scripts parse; CLI help and diff whitespace checks pass.
7. Live RBAC checks and healthy-state verification with separate repair credentials
   pass. The full acceptance attempt reached fixture injection, but OpenRouter
   was unreachable. Cleanup restored the cluster and passed the smoke checks.
8. Automatic approval review rejected a network-enabled retry, requiring explicit
   current-session authorization for exporting bounded demo evidence to
   OpenRouter. Requested that authorization; no workaround or repair approval was
   used. Full live acceptance is pending that authorization and the subsequent
   concrete human approval.

### Current handoff

- **M0–M2 complete; M3 implemented, full live acceptance pending.**
- The cluster is healthy on `incidentpilot-api:m0`.
- Repair command: `uv run --frozen --extra agent python -m agent --repair`.
- Refresh both one-hour credentials with `setup-diagnosis.ps1` and
  `setup-repair.ps1`, then run `verify-repair.ps1` for full acceptance.
- No new incident types, dependencies, database, UI, or infrastructure beyond
  the required repair Role/ServiceAccount were introduced. M4 has not started.

## 2026-10-06 — M4: five reproducible incident classes

1. Read the specification, decisions, history, manifests, source and tests.
   The user explicitly requested M4 despite the earlier pending interactive M3
   acceptance. Recorded the sequencing override before implementation.
2. Added fixed crashloop, OOM, bad-environment and bad-selector injection/restore
   scripts, ground truth and evidence expectations. Central fixture constants
   keep mutations deterministic and reject mixed or foreign configurations.
   Preserved M1 commands and added guards against overlapping M4 incidents.
3. Extended the existing four-tool Kubernetes MCP projection with classified
   startup profiles, only known non-secret DB_HOST values, memory resources,
   current/last termination states, and API Service/owned EndpointSlice evidence.
   Logs can read the previous failed container. Secret contents/references,
   other environment values and arbitrary commands remain excluded.
4. Extended the diagnosis schema/prompt and deterministic citation validator to
   all five classes. Require owned current pods and matching symptoms; reject
   stale events, unrelated logs, mixed configurations and healthy claims during
   Service outages. Added five short runbooks without adding retrieval machinery.
5. Added the remaining four fixed repair actions, strict action-specific
   parameters, human approval, UID/generation/resourceVersion checks, atomic
   patches and recovery verification of rollout, owned ready endpoints and
   database-backed HTTP. Extended only Service/api patch permission on the
   separate runner Role; MCP credentials remain read-only.
6. Added `scripts/verify-incidents.ps1` and the evaluation module. Default mode
   evaluates live model diagnosis/proposal, denial without writes, repeatable
   injection and manual restoration, unrelated resources and full smoke checks.
   Local fixture-only and explicit interactive human-repair modes are available.
   Saved reports cannot authorize execution; there is no automatic approval mode.
7. The user authorized bounded demo evidence export to OpenRouter. Initial
   sandboxed provider calls failed; the authorized network-enabled run succeeded.
   The first live selector case exposed an SDK failure on `endpoints:null`;
   implemented bounded raw-response projection and restored the Service manually.
8. The model initially mistook healthy pods for a healthy service despite the
   broken selector. Evidence validation rejected this and blocked proposals.
   Updated the old deployment-only prompt rule to check routing first. The final
   live evaluation passed all five diagnoses/proposals, denial/no-write checks,
   repeated injection/restoration and all HTTP/database/Prometheus checks. A
   separate final healthy model run produced no proposal or approval request.
9. All 187 tests passed with the existing upstream Starlette/AnyIO warning.
   Tests include every approved action, stale/tampered/expired approval, patch
   conflicts, fixture separation, endpoint ownership, projection privacy and
   verification failures. PowerShell parsing, CLI help, manifest and diff checks
   passed. Live RBAC confirms only the two fixed targets are writable. The final
   provider-free fixture/MCP evaluation also passed all five cases with model
   accuracy correctly unset and the stricter unrelated-resource comparison.
10. Updated README/project status and committed only the non-sensitive summary
    at `eval/results/m4-2026-10-06.json`; raw evidence and credentials stay ignored.
    The demo is healthy and unrelated pods/PVCs preserved. No dependencies,
    incident types beyond the five, UI, infrastructure, or multi-agent system
    were introduced.

### Current handoff

- **M4 complete:** all five incidents reproducible and evaluated with live LLM
  diagnosis/proposals, denied execution and manual verified recovery.
- **Interactive live approved repair remains pending** for M3/all-five execution;
  the evaluation does not count manual restoration as agent repair success.
- Commands: `scripts/verify-incidents.ps1`, `-FixturesOnly`, or `-HumanRepair`.
- Refresh reader/runner tokens with the existing setup scripts. Model/provider
  configuration and the fixed healthy app image remain unchanged.
- After interactive repair acceptance, M5 adds repeated benchmark comparisons,
  metrics, traces and the single Grafana dashboard. Add no incident types.

## 2026-10-06 — M3: approved live recovery and snapshot-check fix

The user ran live M3 acceptance. Denial passed without writes; proposal
`f02bc67cb6213bdd` was human-approved and the restricted runner restored the
API image, advancing generation 46 → 47. The report recorded `recovered`, completed
rollout, one ready replica and successful frontend/database-backed HTTP checks.
The final unrelated-resource assertion failed and manual cleanup left it healthy.

Reproduced a Windows PowerShell 5.1 pipeline bug: `ConvertFrom-Json` emitted the
whole resource array as one object, so the filters retained expected API target
changes. Added `Get-UnrelatedDemoSignature`, which assigns the decoded array
before resource enumeration. It preserves checks on every unrelated workload,
Service (including API), pod and PVC. Added an actual-function AST regression
covering 19 expected-recovery and unrelated-change scenarios. Future runs persist
full before/after snapshots in ignored `.local/m3`.

Validated both saved reports, proposal integrity/citations and the execution
identity/generation; fresh read-only verification using the separate repair
credentials and the full HTTP/Prometheus smoke check passed. Unrelated pod UIDs
match pre-repair evidence. No new injection, provider request, approved repair,
or reused approval was performed. The original full snapshots were not saved,
so the historical full-spec comparison cannot be replayed.

All 188 tests pass with the existing upstream Starlette/AnyIO warning; PowerShell
parsing and diff checks pass. Recorded a non-sensitive M3 result that distinguishes
successful live approved recovery from a clean corrected acceptance-script exit.
The cluster is healthy. A fresh full-script run still needs its own human approval;
the snapshot filter fix and regression validation are complete.

## 2026-10-06 — M3: full live acceptance complete

The user reran `scripts/verify-repair.ps1` and reported `M3 acceptance passed`.
Saved reports confirm denial without execution and human-approved proposal
`dc5970d216d3eef1`, which restored the known-good image with API generation
48 → 49 and completed rollout/endpoints/frontend/database-backed HTTP recovery.
The script's smoke checks and final fixture cleanup passed.

Revalidated report schemas, citations, proposal hash, execution identity/generation
and recovery timing. Compared saved full before/after snapshots with the actual
PowerShell function and an independent parsed-object check: unrelated workloads,
Services, pods and PVC identities/specs match. A fresh read-only check using the
separate runner credentials confirms live health. No provider request, new
injection, repair or approval reuse was performed while recording completion.

Updated the non-sensitive M3 result artifact, project/README status and decisions.
Documentation/result-only change; report/snapshot/live-health checks and Git diff
validation passed. The existing source/test baseline remains 188 passing tests.

### Current handoff

- **M0–M4 complete.** M3's full corrected acceptance script passed with explicit
  human approval; the kind demo is healthy.
- Raw M3 reports and full snapshots remain ignored under `.local/m3`.
- Next: **M5 — repeated evaluation, metrics table, OpenTelemetry traces, one
  Grafana dashboard and README results**. Maintain the five fixed incidents and
  human approval for every agent repair.

## 2026-10-06 — M5: repeated evaluation and local observability

Implemented `scripts/benchmark.ps1` / `eval.benchmark` with repeated five-fixture
episodes, a healthy control, raw/structured/runbook comparison modes, identical
captured evidence, rotated order, deterministic keyword retrieval, candidate and
validated scoring, explicit missing values/denominators, and partial-result
artifacts. Default proposals are denied; every episode restores its fixed fixture
and checks rollout, endpoints, database-backed HTTP, Prometheus, and unrelated
resources. Optional human repair collects fresh evidence/proposals and retains
the exact approval gate and separate runner. Manual cleanup never scores as agent
repair success. Model errors are measurements; harness/safety/export failures
exit nonzero and cleanup failure stops later injection.

Added opt-in OpenTelemetry graph/tool/stage spans, local sanitized JSONL export,
OTLP HTTP export, Prometheus counters/gauges/histograms, and pinned optional
Grafana/Tempo/Pushgateway manifests in a separate namespace. Provisioned one
12-panel dashboard with the existing Prometheus and local Tempo. Setup records
hidden loopback port-forward helpers under `.local/observability`.

Validation: **229 tests pass** (41 new M5 tests); PowerShell scripts parse,
Kustomize renders/applies, lock checks and diff checks pass. Two repeated live
provider-free benchmark runs passed; the final run includes 10 incident episodes
plus one healthy control with supported symptoms, no writes during collection,
verified manual recovery, and preserved unrelated resources. Mean detection
3.720 s, mean manual cleanup 8.355 s, P50/P95 episode duration 11.610/15.813 s.
No telemetry export errors. Prometheus scraped the ten-incident counter, Grafana
provisioned the dashboard, and both Grafana data-source proxies returned real
metrics/stored traces. All seven demo/observability deployments are ready.

Saved non-sensitive summary `eval/results/m5-2026-10-06.json`; raw reports,
metrics exposition, and local traces remain ignored in
`.local/m5/fixtures-final-2026-10-06`. Updated README, decisions, and project status.

Automatic approval review rejected the requested live OpenRouter comparison
because this session did not specifically approve the Kubernetes evidence payload
and external destination. No live model call was made, and no workaround was
attempted. Finish live A/B/C results only after explicit export approval; current
diagnosis accuracy/token/agent-repair metrics are deliberately unset. Optional
approved-repair evaluation remains interactive. M5 implementation and local
acceptance are complete; its live-model acceptance remains pending.

## 2026-10-06 — OpenRouter attribution: named public app

The user's comparison screenshots show a working named Bewerbung Pipeline app
at `https://local.bewerbung/`, whereas IncidentPilot's generic localhost entry
displays its URL and its public app page returns 404. Checked the official
OpenRouter documentation: our `X-OpenRouter-App-Visibility: hidden` suppresses
public app pages, and later headers cannot change an existing app's visibility.
The missing display title's exact cause remains unproven; the prior explanation
that valid headers alone settled the problem was incomplete.

Replaced the generic identifier with `https://local.incidentpilot/`, removed the
hidden visibility header, and sent both `X-OpenRouter-Title` and the supported
legacy `X-Title` as IncidentPilot. Added optional `OPENROUTER_APP_URL`, shared
header construction, and preservation of provider identity in all three
benchmark modes. Updated `.env.example`, README, and decisions. The actual demo
still uses localhost:8080; the named URL is provider attribution metadata.

Validation: **232 tests pass**; outbound default headers, process/file config
precedence, and benchmark identity preservation are covered. One synthetic
`Reply exactly OK.` provider request succeeded. Its generation metadata was
still unavailable after ten read-only polls; no further model request was made.
A separate public-page check returned HTTP 200 with HTML title
`IncidentPilot | OpenRouter`, confirming the named public entry. Diff checks pass.
No incident evidence was exported and no cluster mutation was performed. The
pending M5 live-model benchmark remains pending explicit export approval.

## 2026-10-06 — Isolated attribution test: remove only hidden

At the user's request, reversed the app-specific URL, extra `X-Title`, optional
URL configuration, and benchmark identity plumbing. Compared the result against
the pre-fix revision: the only client-code difference is removal of
`X-OpenRouter-App-Visibility: hidden`. Headers are again
`HTTP-Referer: http://localhost:8080` and `X-OpenRouter-Title: IncidentPilot`.
Updated the existing outbound-header test, README, and decisions; kept previous
experiment history intact.

Validation: **229 tests pass** and diff checks pass. Captured headers from the
actual client through a local mock transport, then used those exact headers for
one explicitly requested synthetic `Reply exactly OK.` OpenRouter request.
At 16:44:29 Europe/Berlin it returned HTTP 200 and `OK`, with 11 prompt tokens,
2 completion tokens, and reported cost $0.0000076. Generation:
`gen-1791297870-iwoGzFx7W1HMgVKqZQUN`. The credential-free result is saved in
ignored `.local/openrouter-hidden-only.json`. No incident evidence or cluster
action was involved. Leave the isolated variant in place for the user's activity
display-name confirmation. Public-page visibility for the already-created hidden
localhost entry is not reset by this test. M5 live benchmark remains pending.

## 2026-10-06 — Hidden-only name failure; isolated X-Title test

The user supplied the hidden-only request's activity screenshot: it still shows
`http://localhost:8080/` as the name, while the preceding combined-fix request
shows IncidentPilot. Removing hidden alone did not fix the observed name.

For the next controlled test, retained `HTTP-Referer: http://localhost:8080` and
`X-OpenRouter-Title: IncidentPilot`, kept hidden absent, and added only the
documented compatible `X-Title: IncidentPilot`. No app URL/configuration or
benchmark changes were reintroduced. Updated the existing header check and
current README/decision record.

All **25 diagnosis tests pass**; diff checks pass. Captured the actual client's
headers locally and sent one synthetic `Reply exactly OK.` request with exactly
this attribution variant. At 16:48:09 Europe/Berlin it returned HTTP 200 and OK,
with 11 prompt/2 completion tokens and cost $0.0000076. Generation:
`gen-1791298089-6AZJTwEqdfQyucA8dmI3`; credential-free result is ignored in
`.local/openrouter-title-only.json`. The activity display-name result awaits
the user's confirmation; HTTP success is not treated as proof of attribution.
No incident evidence or cluster action was involved.

## 2026-10-06 — Final attribution: app-specific identity with hidden enabled

The user confirmed the compatible X-Title/localhost test still displays the URL,
and requested a final proper-name check with hidden enabled. Initially inferred
a fresh private identifier; the first preflight incorrectly treated an HTTP 200
response as an existing public app and stopped before any model request. The
user clarified that the existing working named URL was intended. Restored
`https://local.incidentpilot/` with hidden and sent the requested synthetic test
at 16:57:50 Europe/Berlin, HTTP 200/OK, 11 prompt/2 completion tokens; generation
`gen-1791298670-cuU269r9XicVRpR2iZdk` in ignored
`.local/openrouter-final-hidden.json`.

The user then explicitly chose the fresh private identifier approach. The
candidate page's actual title was `Not Found | OpenRouter`, despite HTTP 200,
and contained no IncidentPilot name. Configured
`HTTP-Referer: https://local.incidentpilot-private/`, both IncidentPilot title
headers, and `X-OpenRouter-App-Visibility: hidden` from its first model request.
This preserves the previous public entry and requests a distinct hidden entry.

Final synthetic test at 16:59:28 Europe/Berlin returned HTTP 200/OK with 11
prompt/2 completion tokens and cost $0.0000076; generation
`gen-1791298768-ziFeD42asqOqk1Gitx5H`. Public-page content remained
`Not Found | OpenRouter`, consistent with hidden visibility. Credential-free
result is ignored under `.local/openrouter-private-final.json`. The name in
private account analytics awaits the user's confirmation; HTTP status/title
checks are not substituted for that observation.

All **229 tests pass**; captured outbound headers and diff checks pass. Updated
README and decision record. No incident evidence or cluster mutation was involved;
M5's live benchmark remains pending explicit evidence-export approval.

## 2026-10-06 — Resolve final attribution screenshot's URL mismatch

The user's latest screenshot shows IncidentPilot but its tooltip URL is
`https://local.incidentpilot/`, the existing entry used by the 16:57:50 test.
That test did carry hidden, so the screenshot confirms the existing entry's
proper name persisted; it does not confirm the fresh private entry's activity
name or establish a visibility change for the existing entry.

Read only the two already-sent generations via OpenRouter's authenticated
generation-metadata endpoint, with no new model request or incident export.
Both metadata lookups returned HTTP 200. The existing named test has app ID
5231381 and origin `https://local.incidentpilot/`. The 16:59:28 fresh hidden test
has a distinct app ID 5231546 and origin
`https://local.incidentpilot-private/`. Thus the private request was attributed
to a separate app as configured, not grouped into the previous public entry.
Its public-page content remains unavailable in the earlier check; its private
activity display name still awaits observation. Safe metadata is ignored in
`.local/openrouter-attribution-inspection.json`.

Updated README/handoff with that distinction. Documentation-only change;
metadata consistency and diff checks pass. Runtime configuration remains the
named private identifier with hidden; the last full code validation is 229 tests.

## 2026-10-06 — Named private attribution confirmed

The user supplied the final activity screenshot showing IncidentPilot with the
fresh `https://local.incidentpilot-pri...` tooltip, confirming the proper display
name for the separate private entry whose app ID/origin were already verified.
Keep `https://local.incidentpilot-private/`, both IncidentPilot title headers,
and hidden enabled. Attribution investigation is complete: both removing hidden
alone and adding the compatible title header on localhost failed, while the
app-specific identifier works with hidden enabled from its first request.

Updated README and handoff to replace the pending name check with the user's
confirmation. No further provider request, cluster action, or runtime change.
Documentation diff checks pass; the latest code validation remains 229 passing
tests. M5's incident-evidence benchmark is still separately pending authorization.

## 2026-10-06 — M5: authorized live A/B/C comparison complete

Read the specification, decisions, and implementation history; confirmed that
the next unfinished acceptance was the live M5 comparison and that private
OpenRouter attribution was already resolved. All 229 tests passed; the demo
smoke check, seven deployment readiness checks, and local observability endpoints
passed. The user explicitly approved exporting bounded demo Kubernetes state,
events, and API logs to OpenRouter for the 33-request benchmark.

Refreshed the separate read-only credentials. The first restricted run received
no provider responses, restored every fixture, preserved unrelated resources,
and exported telemetry without errors; retain it under ignored
`.local/m5/20261006-153707`. The authorized network-enabled retry of
`scripts/benchmark.ps1 -Repetitions 2 -Observability` exited successfully with
`openai/gpt-4.1-mini`, 10 incident episodes, and a shared healthy control.

Each of raw, structured, and runbook modes achieved 10/10 candidate and supported
diagnoses, 100% top-2 recall and required citation coverage, and zero unsupported
claims. All three healthy-control responses produced no proposal. All 30 incident
proposals were denied, with no execution stage or execution data; all 11 episodes
preserved unrelated resources and passed verified manual restoration.

Mean tokens per comparison including controls were 8,755.273 / 9,705.909 /
10,082.000 for A/B/C, with complete usage coverage. P50/P95 comparison latencies
were 2.531/3.797, 2.500/3.515, and 2.547/3.860 s. Mean symptom detection was
3.683 s; mean manual cleanup was 8.373 s. P50/P95 full incident duration was
20.437/24.953 s. This small sample shows equal accuracy; retrieval adds tokens
without demonstrating accuracy improvement. Diagnosis times include mode-order
waiting and do not establish a speed benefit.

Independently validated all 33 report schemas, scores, denied/not-needed outcomes,
and absence of execution; recomputed aggregate metrics. Checked all 709 local
trace spans, 11 incident roots, and 33 comparison/diagnosis spans without errors.
Grafana's 12-panel dashboard and both data-source proxies passed: Prometheus
returned ten injected incidents and 33 correct diagnoses, and Tempo returned
the final stored trace. A fresh HTTP/database/Prometheus smoke check and all seven
deployment readiness checks passed. Zero telemetry export errors.

Published the credential/evidence-free summary at
`eval/results/m5-live-2026-10-06.json`, preserving the provider-free artifact.
Raw reports, traces, metrics, and independent validation remain ignored under
`.local/m5/20261006-153954`. Updated README, decisions, and milestone status.
No application, dependency, provider attribution, or incident-type changes.

### Current handoff

- **M0–M5 complete.** Live A/B/C diagnosis metrics are published; cluster healthy.
- Next unfinished measurement: approved live repair success, MTTR, and immediate
  regression across the five fixtures using existing interactive `-HumanRepair`.
  Manual cleanup is not agent repair success; each fresh repair requires exact
  human approval. The live export approval does not approve repairs.
- Remaining portfolio checks stay in `project.md` Definition of Done. Keep the
  five fixed incidents and existing safety boundary; add no unrelated systems.
