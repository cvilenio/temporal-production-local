# Architecture

This repository is a Temporal demonstration platform built to mirror **production
DevOps/Platform concerns** as closely as possible without renting a cloud provider for
the customer-owned components. It hosts a retail order-processing workflow, a real-time
operator console, supporting services, and the infrastructure to run them on local
Kubernetes (kind) against either a self-hosted Temporal server or Temporal Cloud.

The design has four goals, in priority order:

1. **Realistic production lifecycle** — self-hosted cluster ops *and* Temporal Cloud,
   GitOps delivery, worker versioning, observability.
2. **Polyglot growth** — Python today; Go, TypeScript, and Java workers/activities later,
   without reorganizing the repo again.
3. **Shared-kernel ergonomics** — common code lives in one place per language; each
   deployable app is a thin definition on top of it.
4. **No rabbit holes** — favor the smallest toolchain that achieves the above reliably.

---

## Two planes

The system separates cleanly into two planes. This separation is the spine of the whole
design.

| Plane | What it is | Where it runs | Lifecycle tool |
|---|---|---|---|
| **Control plane** | The kind cluster itself + Temporal Cloud (namespaces, API keys) + ArgoCD install | Provisioned once | **Terraform** |
| **Customer-owned plane** | Workers, apps, codec server, observability — and (by default) a self-hosted Temporal server | On kind | **ArgoCD → Helm** |

The Temporal **server** is the swappable backend of the customer-owned plane:

```
profile = local-k8s  →  Temporal server (official Helm chart, CNPG-backed) on kind   [default]
profile = cloud      →  Temporal Cloud (external; namespace + API key managed by Terraform)
                         workers / apps / codec / observability always run on kind
```

The application code does not know or care which backend is active — it is selected
entirely by the connection profile (env), see [Connection profiles](#connection-profiles).

---

## Repository layout

```
apps/                         THIN deployment units — what you DEPLOY. Grouped by class.
  temporal/                     Temporal platform deploys.
    workers/python/
      workflow/  main.py          run_worker("workflow")
      activity/  main.py          run_worker("activity")
      (activity-cpu/, activity-io/ … add a profile + a dir — see Worker fleet)
    codec-server/python/        Temporal-adjacent: remote codec proxy (scaffold).
  business/                     Customer-like apps (business logic; only a Temporal client).
    orders-api/python/            REST gateway that starts/signals workflows.
  demo/                         Not required to run Temporal in prod (local/demo tooling).
    console/python/               Operator UI (HTMX + SSE).
    mock-api/python/              External-system simulator.

libs/                         Shared code — what apps IMPORT. Use case ABOVE language.
  orders/                       the orders domain (polyglot pieces stay together).
    python/                       uv workspace package `orders`.
      orders/                workflows/ activities/ clients/ db/ shared/
                                    services/ config.py containers.py resources.py
                                    api.py (FastAPI app)  worker.py (profiles + runner)
      pyproject.toml                Owns the concrete library versions (single source of truth).
    go/  typescript/              (future) same use case, other languages — side by side.

images/
  python.Dockerfile             Configurable image; build args pick the dep group +
                                entrypoint. Kernel always present so the workspace
                                resolves. App definitions stay lightweight. (one per language)

deploy/
  terraform/                    Control plane: kind cluster, Temporal Cloud, ArgoCD install.
  argocd/                       App-of-apps + per-workload Application manifests.
  charts/                       Helm charts for every workload on kind.

config/                       Connection profiles (local-k8s | cloud) → env.
compose/                      Self-hosted Temporal + observability for the fast,
                              no-Kubernetes local quick-start (docker-compose.yml).
docs/  ai_checkpoints/        Design docs + ADRs; cross-session work log.
pyproject.toml  uv.lock       Python workspace anchor (root).
```

Top level reads at a glance: **`apps/`** (what you deploy, grouped by class:
Temporal / business / demo) · **`libs/`** (what they share) · **`images/`** (how they
build) · **`deploy/`** (how they ship). The deployable-vs-library line uses the names a
newcomer already knows (`apps/` + `libs/`, per the Nx / uv monorepo convention).

### Why shared-kernel + thin apps

The kernel holds everything reusable (workflow definitions, activity implementations,
clients, DB models, telemetry, the API app factory, the worker runner). A deployable app
is a few lines that import the kernel and start one thing. This matches the proven
"shared base image + thin app" pattern and the official samples:

- **Python** — `bedrock/shared/` subpackage + per-app `TASK_QUEUE` constants
  (`samples-python`).
- **TypeScript** — `monorepo-folders/packages/temporal-workflows` barrel files consumed by
  a thin `temporal-worker` (`samples-typescript`).
- **Java** — root `build.gradle subprojects{}` shared config + a `core` module
  (`samples-java`).
- **Go** — lib package + thin `worker/main.go` + `constants.go` (`samples-go`).

The image follows the same shape: `images/python.Dockerfile` always installs the kernel
(the "base") and then copies a thin app directory (the "definition"). Build args select
the uv dependency group and the entrypoint, so each app's footprint in the Dockerfile is
zero — only `docker-compose.yml` / the Helm chart names them. One configurable image per
language (`images/<lang>.Dockerfile`).

---

## Worker fleet (scales per language and per resource profile)

A worker is described by a **profile** in `orders.worker.WORKER_PROFILES`:

```python
WorkerProfile(name, task_queue, workflows=[...], activity_groups=(...))
```

Each profile maps to one thin app under `apps/temporal/workers/<lang>/<name>/`. Today:

| Profile | Task queue | Hosts |
|---|---|---|
| `workflow` | `orders-workflow-task-queue` | `OrderWorkflow` |
| `activity` | `orders-activity-task-queue` | external + persistence + customer-message activities |

Adding a worker is **additive** — register a profile, add a sibling app dir. This is how a
**CPU-bound** activity worker lives alongside an **IO-bound** one: separate profiles,
separate task queues, scaled and tuned independently. The kernel comments show the
`activity-cpu` / `activity-io` extension. Nothing in the kernel changes.

---

## Worker versioning

Versioning uses the modern **Worker Deployment Versioning** model (Server ≥ 1.28 / CLI ≥
1.4; our server is 1.31, SDK pinned `temporalio>=1.28,<1.29`).

- **Deployment identity is a worker option.** `orders.worker` reads
  `TEMPORAL_DEPLOYMENT_NAME` and `TEMPORAL_WORKER_BUILD_ID` from the environment and, when
  present, builds a `WorkerDeploymentConfig`. Absent (local/compose), the worker stays
  version-agnostic — behavior is unchanged.
- **Behavior is a per-workflow declaration.** `PINNED` (in-flight executions never replay
  against new code) vs `AUTO_UPGRADE` (migrate to current) is set on the workflow
  definition — *not* yet enabled on `OrderWorkflow`; see ADR-0004 for the planned default.
- **On Kubernetes** the **Temporal Worker Controller** (`kind: WorkerDeployment` CRD)
  injects those env vars and derives the Build ID from the pod-template hash. Shipping a
  new version is therefore just a new **image tag** — no manifest edits to the worker spec.
  Rollout (ramp / promote / rollback) is driven either by the controller's `Progressive`
  strategy (GitOps owns the ramp) or the deployment API (`SetCurrentVersion` /
  `SetRampingVersion`).

See ADR-0004 and `deploy/charts/orders-workers`.

---

## Observability

Unchanged in model from the current stack (see `OBSERVABILITY.md`), carried onto kind:

- **PUSH (OTLP gRPC):** traces → Tempo, logs → Loki, business metrics → Prometheus, via the
  OpenTelemetry Collector. `orders.shared.telemetry` initializes the providers and
  the Temporal `Runtime`.
- **PULL (Prometheus scrape):** Temporal SDK operational metrics + Temporal server metrics.
- On kind, the colleague reference (`alexandreroman/temporal-k8s`) supplies a turnkey
  **backlog-driven worker autoscaler**: PrometheusRule recording rules → prometheus-adapter
  external metrics → HPA on task-queue depth. Captured in `deploy/charts/observability`.
- On Cloud, add the Cloud OpenMetrics endpoint as a scrape target.

---

## Connection profiles

`orders.config.Settings` is the single source of connection config (one-stop
config). Local is the default; Cloud is opt-in via env:

| Setting (env) | Local default | Temporal Cloud |
|---|---|---|
| `TEMPORAL_ADDRESS` | `localhost:7233` / `temporal:7233` | `<ns>.<account>.tmprl.cloud:7233` |
| `TEMPORAL_NAMESPACE` | `ziggymart` | `<ns>.<account>` |
| `TEMPORAL_TLS` | `false` | `true` |
| `TEMPORAL_API_KEY` | — | API key (or use mTLS below) |
| `TEMPORAL_TLS_CLIENT_CERT_PATH` / `_KEY_PATH` | — | mTLS client cert + key |

`TemporalService.connect()` builds `tls`/`api_key`/`TLSConfig` from these. The profile
bundles live in `config/`. Credentials never go in git (see `.gitignore`).

---

## Local quick-start vs. the full lifecycle

Two ways to run, by intent:

- **`docker-compose.yml`** — fastest path; self-hosted Temporal + the full app + LGTM
  observability, no Kubernetes. Best for app development and demos.
- **kind + Terraform + ArgoCD** (`deploy/`) — the production-like lifecycle; self-hosted
  Temporal on k8s (or Cloud), GitOps delivery, worker versioning, autoscaling. Best for
  platform/ops demos and readiness work.

Both run the **same images and the same kernel**; only the delivery layer differs.

---

## Decisions

See `docs/adr/` for the rationale behind each choice (each is a short, dated record):

- ADR-0001 — Polyglot shared-kernel monorepo layout
- ADR-0002 — Infrastructure & delivery: Terraform control plane + ArgoCD/Helm
- ADR-0003 — Temporal server backend: self-hosted on kind, Cloud-switchable
- ADR-0004 — Worker versioning via the Temporal Worker Controller
- ADR-0005 — Temporal connection profiles (local ↔ Cloud)
- ADR-0006 — Standalone codec server + data-converter encryption
