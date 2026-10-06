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
