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
