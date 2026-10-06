# IncidentPilot — Autonomous Kubernetes Incident Response Agent

> **Project status:** M0 complete and verified locally; next milestone is M1
> **Primary goal:** Build a controlled incident-response agent that diagnoses reproducible Kubernetes failures, proposes a repair with evidence, requires human approval, executes a limited repair action, and verifies recovery.

---

## 0. Session Bootstrap Contract

**Every new coding/agent session MUST start here.**

Before coding:

1. Read this entire `project.md`.
2. Read `DECISIONS.md` if present.
3. Inspect the current cluster manifests, source code and tests.
4. Identify the current milestone.
5. Implement only the next unfinished acceptance criterion.
6. New incident types, tools, agents, databases and UIs are forbidden unless the current milestone explicitly requires them.
7. Put useful future ideas in **Parking Lot**.
8. Do not convert this into a generic DevOps dashboard or autonomous self-healing platform.

### Immutable project statement

IncidentPilot diagnoses a small, fixed set of Kubernetes incidents using metrics, events and logs, proposes an evidence-backed repair, executes only approved low-risk actions, and measures diagnosis and recovery performance.

### Scope-change rule

Any change to the MVP must be documented in `DECISIONS.md` before implementation.

Valid reasons:

- acceptance criteria are impossible with the current design
- a required dependency is unavailable
- evidence from tests shows the current architecture cannot meet the goal

Invalid reasons:

- another framework looks interesting
- a new model was released
- “production systems would also need X”

---

# 1. Problem

Kubernetes incidents require engineers to combine evidence from:

- pod state
- events
- logs
- metrics
- deployment configuration
- recent changes

LLMs can reason across those sources, but uncontrolled autonomous remediation is dangerous.

The project asks:

> **Can an agent reduce time-to-diagnosis and time-to-recovery for a fixed set of Kubernetes failures without hiding uncertainty or executing unapproved changes?**

---

# 2. Portfolio Demo

The final demo:

```text
healthy app
   ↓
inject known failure
   ↓
alert fires
   ↓
IncidentPilot investigates
   ↓
evidence + root-cause hypothesis
   ↓
proposed repair
   ↓
human approval
   ↓
repair
   ↓
health verification
   ↓
incident report
```

This is not a chat interface with `kubectl`.

It is a measurable incident-response workflow.

---

# 3. MVP Environment

Use one local Kubernetes cluster.

Preferred for development:

- `kind` or `k3d`

Choose **one** and document the choice in `DECISIONS.md`.

Do not support multiple cluster distributions in MVP.

## Demo application

Small three-service application:

```text
frontend
   ↓
api
   ↓
postgres
```

Optional Redis is **not** part of MVP.

Instrumentation:

- Prometheus metrics
- logs
- Kubernetes events
- health endpoints

---

# 4. Incident Library — Hard Limit

MVP supports exactly five incident classes:

1. `CrashLoopBackOff`
2. `OOMKilled`
3. broken environment variable / secret
4. invalid Service selector
5. bad container image tag

Do not add DNS, disk, network policy, CPU saturation, certificate expiry or other incidents until all five work end to end.

Each incident must have:

```text
incident_id
injection script
observable symptoms
ground-truth root cause
approved repair action
verification condition
```

---

# 5. Explicit Non-Goals

Not in MVP:

- production cloud cluster
- AWS/GCP/Azure integration
- multi-cluster support
- fully autonomous remediation
- shell access for the LLM
- arbitrary `kubectl`
- Terraform
- ArgoCD
- on-call paging integration
- Slack/Teams bot
- long-term agent memory
- multi-agent system
- self-modifying runbooks
- model fine-tuning
- large frontend
- LogHub ingestion
- chaos engineering platform

Some may become stretch goals later.

---

# 6. Architecture

```text
                      ┌───────────────┐
Alert / User ────────►│ LangGraph     │
                      │ Incident Agent │
                      └───────┬───────┘
                              │
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
          Kubernetes MCP  Metrics MCP   Logs MCP
                 │            │            │
                 └──────┬─────┴─────┬──────┘
                        ▼           ▼
                    Evidence Collector
                           │
                           ▼
                    Root Cause Hypothesis
                           │
                           ▼
                     Repair Proposal
                           │
                           ▼
                     HUMAN APPROVAL
                           │
                           ▼
                 Restricted Action Runner
                           │
                           ▼
                       Verification
```

---

# 7. Agent State Machine

Use LangGraph because the workflow is naturally stateful.

```text
DETECT
  ↓
COLLECT
  ↓
HYPOTHESIZE
  ↓
TEST
  ↓
PROPOSE_FIX
  ↓
WAIT_FOR_APPROVAL
  ↓
EXECUTE
  ↓
VERIFY
  ↓
REPORT
```

The graph must not jump directly from hypothesis to execution.

---

# 8. MCP Tools

Keep the tool surface small.

## kubernetes-mcp

```text
get_pods(namespace)
describe_pod(namespace, pod)
get_events(namespace)
get_deployment(namespace, name)
```

## metrics-mcp

```text
query_metric(query)
```

## logs-mcp

```text
search_logs(service, since_minutes, contains?)
```

## repair service

Do **not** expose unrestricted shell.

Allowed MVP repair functions:

```text
restart_deployment()
rollback_image_tag()
restore_known_env_value()
restore_known_service_selector()
increase_memory_limit_to_known_safe_value()
```

Repair functions use deterministic validation.

---

# 9. Core Tech Stack

- Python 3.12
- LangGraph
- official Python MCP SDK
- Pydantic
- Kubernetes Python client
- FastAPI for agent API
- Prometheus
- Grafana
- Loki or a simpler log backend — choose one
- OpenTelemetry
- pytest
- Docker
- Helm only after the application is stable

Do not introduce:

- CrewAI
- AutoGen
- multiple vector DBs
- Kafka
- Elasticsearch

unless an existing acceptance criterion cannot be met.

---

# 10. Data

## 10.1 Primary data

The primary dataset is **self-generated, reproducible incidents**.

This is preferable for the MVP because every case has known ground truth.

Structure:

```text
incidents/
├── crashloop/
├── oom/
├── bad_env/
├── bad_selector/
└── bad_image/
```

Each contains:

```text
inject.sh
restore.sh
ground_truth.yaml
expected_evidence.yaml
```

## 10.2 External dataset — stretch only

**LogHub** may later be used for offline log anomaly experiments.

It is not required for the MVP.

Do not ingest millions of log lines before the live incident workflow works.

---

# 11. Evidence Model

```python
class Evidence(BaseModel):
    source: Literal["kubernetes", "events", "logs", "metrics"]
    query: str
    observation: str
    timestamp: datetime | None
```

Root-cause output:

```python
class Diagnosis(BaseModel):
    incident_type: str
    suspected_root_cause: str
    confidence: float
    evidence_ids: list[str]
    alternative_hypotheses: list[str]
```

Repair proposal:

```python
class RepairProposal(BaseModel):
    action: str
    parameters: dict
    expected_effect: str
    risk: Literal["low", "medium", "high"]
    evidence_ids: list[str]
```

---

# 12. Evaluation

Every incident replay is an evaluation episode.

## Diagnosis metrics

- root-cause accuracy
- top-2 root-cause recall
- evidence completeness
- unsupported diagnosis rate

## Repair metrics

- successful repair rate
- unsafe action rate
- unnecessary repair rate
- regression rate

## Operational metrics

- MTTD — mean time to detect
- MTTDg — mean time to diagnose
- MTTR — mean time to recover
- tool calls per incident
- tokens per incident
- P50/P95 incident duration

## Comparison

Minimum comparison:

```text
A. LLM with raw prompt only
B. agent with structured tools
C. structured agent + runbook retrieval
```

Do not add memory until this comparison exists.

---

# 13. Runbooks

Start with five short Markdown runbooks, one per incident type.

Example:

```text
runbooks/
├── crashloop.md
├── oom.md
├── bad_env.md
├── bad_selector.md
└── bad_image.md
```

Runbooks contain:

- symptoms
- checks
- likely root causes
- permitted repair actions
- verification

Retrieval can initially be simple keyword/BM25.

A vector database is not required for five files.

---

# 14. Observability

The project itself must be observable.

## OpenTelemetry trace

```text
incident
├─ alert
├─ get_pods
├─ get_events
├─ logs
├─ metrics
├─ diagnosis
├─ repair proposal
├─ approval
├─ repair
└─ verification
```

## Prometheus metrics

```text
incident_total
diagnosis_correct_total
repair_success_total
unsafe_action_total
tool_call_total
agent_latency_seconds
tokens_total
```

## Grafana

One dashboard only for MVP:

- active incident
- service health
- pod status
- error rate
- diagnosis stage
- MTTR
- latest agent actions

---

# 15. Safety Boundary

The agent may **read freely** from the demo cluster.

The agent may **write only through an allowlisted action runner**.

Forbidden:

```text
arbitrary shell
kubectl exec
delete namespace
delete PVC
arbitrary patch
arbitrary Helm operation
```

Every repair requires human approval in MVP.

This boundary is part of the portfolio story.

---

# 16. Repository Structure

```text
incidentpilot/
├── project.md
├── README.md
├── DECISIONS.md
├── pyproject.toml
├── app/
│   ├── frontend/
│   ├── api/
│   └── db/
├── cluster/
│   ├── base/
│   └── incidents/
├── agent/
│   ├── graph.py
│   ├── models.py
│   ├── diagnosis.py
│   └── repair.py
├── mcp/
│   ├── kubernetes_server.py
│   ├── metrics_server.py
│   └── logs_server.py
├── runbooks/
├── eval/
├── telemetry/
└── tests/
```

---

# 17. Milestones

## M0 — Healthy Demo App

Deliver:

- local cluster
- frontend/api/postgres running
- health check
- one Prometheus metric
- reproducible setup command

**Acceptance:** fresh checkout reaches healthy state with one documented command sequence.

**Status:** Complete, verified 2026-10-06. A clean Git checkout created an empty
kind cluster and passed the frontend/API/PostgreSQL and Prometheus smoke checks.
Repeating setup preserved the PostgreSQL pod and PVC and passed verification.

---

## M1 — One Incident, No AI

Implement only:

`bad image tag`

Deliver:

- injection script
- observable failure
- deterministic restore script
- ground truth

**Acceptance:** incident can be injected and restored repeatedly.

---

## M2 — Read-Only Agent Diagnosis

Deliver:

- Kubernetes MCP
- events/logs tools
- LangGraph `COLLECT → HYPOTHESIZE`
- diagnosis for bad-image case

**Acceptance:** agent identifies the correct root cause and cites evidence.

No repair yet.

---

## M3 — Human-Approved Repair

Deliver:

- repair proposal
- approval gate
- restricted repair runner
- verification

**Acceptance:** agent proposes correct repair; nothing is changed before approval; cluster returns healthy after approval.

---

## M4 — Five Incident Types

Add the remaining four incident classes.

**Acceptance:** all five are reproducible and evaluated.

---

## M5 — Evaluation + Observability

Deliver:

- repeated incident runs
- metrics table
- OpenTelemetry traces
- Grafana dashboard
- README results

**Acceptance:** one script runs the benchmark and produces a result artifact.

---

# 18. First Five Commits

```text
1. chore: initialize IncidentPilot repository
2. feat: deploy minimal demo application to local Kubernetes
3. feat: add bad-image incident injector and restore script
4. feat: expose read-only Kubernetes inspection tools
5. feat: diagnose bad-image incident with structured evidence
```

Do not start with LangGraph before the failure is reproducible manually.

---

# 19. Definition of Done

Portfolio-ready means:

- [ ] five incidents reproducible
- [ ] read-only evidence collection works
- [ ] structured diagnosis works
- [ ] repair requires approval
- [ ] repairs use allowlisted actions
- [ ] recovery is automatically verified
- [ ] benchmark is reproducible
- [ ] diagnosis/repair metrics published
- [ ] traces visible
- [ ] dashboard works
- [ ] architecture diagram exists
- [ ] demo completes in <5 minutes
- [ ] clean checkout instructions work

---

# 20. Parking Lot

- LogHub
- incident memory
- similarity retrieval from old incidents
- Slack integration
- PagerDuty
- ArgoCD
- Terraform
- autonomous low-risk remediation
- multi-cluster
- multi-agent investigation
- chaos framework
- cloud deployment
- Kubernetes operator

---

# 21. New-Session Prompt

> Read `project.md` and `DECISIONS.md` before changing code. Treat the five-incident MVP, safety boundary, non-goals, state machine and milestone acceptance criteria as authoritative. Inspect the current repository and cluster state, determine the next unfinished acceptance criterion, and implement only that. Do not add incident types or infrastructure from the Parking Lot. If a change to the plan is necessary, propose a `DECISIONS.md` entry first.

---

# 22. Immediate Next Action

**Do not begin with the AI agent.**

Begin with:

```text
local Kubernetes
→ minimal application
→ healthy state
→ inject bad image tag
→ observe Crash/Pull failure
→ restore healthy state
```

Until this loop is deterministic, there is nothing useful for an agent to diagnose.
