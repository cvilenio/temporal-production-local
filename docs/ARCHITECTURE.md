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
apps/                         DEPLOYABLE assembly — what you DEPLOY. Grouped by class.
  temporal/                     Orchestration substrate — required for workflows to run.
    workers/python/               each worker app = settings.py + dependencies.py + main.py
      <domain>/                   one dir per domain (e.g. orders/)
        workflow/                   hosts OrderWorkflow (wires no activity ports)
        activity/                   hosts the activities (wires mock-api/orders-service ports)
        (activity-cpu/, activity-io/ … add a sibling profile dir — see Worker fleet)
    codec-server/python/        Temporal-adjacent: remote codec proxy (scaffold).
  platform/                     Platform/operability tooling (not required by business logic).
    console/python/               Host-plane operator UI (HTMX + SSE); aggregates infra UIs.
  business/                     Temporal-agnostic domain apps + simulated integrations.
    orders-api/python/            REST gateway (FastAPI): settings/dependencies/main + routes/.
    mock-api/python/              External-system simulator: settings/dependencies/main + routes/.

libs/                         REUSABLE, not-deployable — what apps IMPORT (ADR-0022).
  orders/                       domain core (the orders domain; polyglot pieces together).
    python/                       uv package `orders`: workflows/ activities/ clients/
      orders/                     db/models.py shared/ (contracts, ids, errors). NO env
                                    reads, client/DB construction, or process lifecycle.
      pyproject.toml                Domain deps only (single source of truth for them).
    go/  typescript/              (future) same domain, other languages — side by side.
  appkit/                       generic composition kit (domain- & app-agnostic, class 3a):
    python/                       uv package `appkit`: build_temporal_client (bakes the
                                  data-converter contract), SQL engine factory, telemetry
                                  bootstrap, run_worker loop, settings field-groups.
  logging/                      uv package `obslog` — the structured-logging kernel (ADR-0018).

images/
  python.Dockerfile             Configurable image; build args pick the dep group +
                                entrypoint. Kernel always present so the workspace
                                resolves. App definitions stay lightweight. (one per language)

deploy/
  terraform/                    Control plane: kind cluster, Temporal Cloud, ArgoCD install.
  argocd/                       App-of-apps + per-workload Application manifests.
  charts/                       Helm charts for every workload on kind.

config/                       Connection profiles (local-k8s | cloud) → env.
compose/                      Host visibility/console plane for the kind paths + a legacy
                              local self-hosted Temporal server + app tier (no workers).
docs/  ai_checkpoints/        Design docs + ADRs; cross-session work log.
pyproject.toml  uv.lock       Python workspace anchor (root).
```

Top level reads at a glance: **`apps/`** (what you deploy, grouped by class:
Temporal / business / demo) · **`libs/`** (what they share) · **`images/`** (how they
build) · **`deploy/`** (how they ship). The deployable-vs-library line uses the names a
newcomer already knows (`apps/` + `libs/`, per the Nx / uv monorepo convention).

### Why domain core + generic kit + app composition (ADR-0022)

`/libs` is **reusable and not deployable**, in two species: **domain cores** (`libs/orders` —
workflow/activity definitions, client ports, proto contracts, DB models, ids/errors; no env,
no client construction, no lifecycle) and a **generic composition kit** (`libs/appkit` —
the Temporal client builder that bakes in the data-converter contract, the SQL engine factory,
the telemetry bootstrap, the `run_worker` loop, and settings field-groups; names no workflow or
service). `/apps` is the **deployable assembly**: each app has a `settings.py` / `dependencies.py`
(composition root) / `main.py`, wires only the ports it uses, and chooses its own provider
lifetimes — while *consuming* the shared contracts (data converter, queue/namespace/SA keys),
never re-deciding them. The earlier "fat kernel + thin apps" (ADR-0001) put composition in the
lib; ADR-0022 moved it to the apps + the kit. The reusable-code-by-use-case-then-language idea
still matches the official samples:

- **Python** — `bedrock/shared/` subpackage + per-app `TASK_QUEUE` constants
  (`samples-python`).
- **TypeScript** — `monorepo-folders/packages/temporal-workflows` barrel files consumed by
  a thin `temporal-worker` (`samples-typescript`).
- **Java** — root `build.gradle subprojects{}` shared config + a `core` module
  (`samples-java`).
- **Go** — lib package + thin `worker/main.go` + `constants.go` (`samples-go`).

The image follows the same shape: `images/python.Dockerfile` installs the app's uv
dependency group (which pulls the `libs/` it needs — always the full `libs/` tree is copied
so the workspace resolves) and then copies the app directory (its composition root +
entrypoint). Build args select the dependency group and the entrypoint, so each app's
footprint in the Dockerfile is zero — only `docker-compose.yml` / the Helm chart names them.
One configurable image per language (`images/<lang>.Dockerfile`).

---

## Worker fleet (scales per language and per resource profile)

Each worker is its own deployable app under `apps/temporal/workers/<lang>/<name>/`, whose
`main.py` builds a Temporal client via `appkit.build_temporal_client` and runs the generic
`appkit.run_worker` loop with the task queue, workflows, and activities it declares inline
(`appkit.WorkerProfile` is the data shape). Today:

| Profile | Task queue | Hosts |
|---|---|---|
| `workflow` | `orders-workflow-task-queue` | `OrderWorkflow` |
| `activity` | `orders-activity-task-queue` | external + persistence + customer-message activities |

Adding a worker is **additive** — add a sibling app dir that wires its own ports and task
queue. This is how a **CPU-bound** activity worker lives alongside an **IO-bound** one:
separate apps, separate task queues, scaled and tuned independently. `libs/orders` (the
domain) and `libs/appkit` (the kit) don't change.

---

## Worker versioning

Versioning uses the modern **Worker Deployment Versioning** model (Server ≥ 1.28 / CLI ≥
1.4; our server is 1.31, SDK pinned `temporalio>=1.28,<1.29`).

- **Deployment identity is a worker option.** `appkit.build_deployment_config` reads
  `TEMPORAL_DEPLOYMENT_NAME` and `TEMPORAL_WORKER_BUILD_ID` from the environment and, when
  present, builds a `WorkerDeploymentConfig` (each worker app calls it). Absent (local/compose), the worker stays
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

Two stores, split by signal *purpose* — high-fidelity analytical signals warehouse in
ClickHouse; low-cardinality operational signals + autoscaling live in Prometheus. `appkit`'s
telemetry bootstrap (`init_observability`) wires the providers and the Temporal `Runtime`;
each app starts it in its lifecycle.

- **PUSH (OTLP gRPC):**
  - **Traces → Tempo** (lgtm), on `:4317`.
  - **Logs → ClickHouse** (`otel_logs`) via the standalone OTel Collector — Loki retired
    (ADR-0020). On kind, Alloy tails pod stdout → collector.
  - **Business metrics → ClickHouse** (`otel_metrics_*`) via the same collector, on a
    *separate* endpoint (`:4319`) with DELTA temporality (ADR-0024). Emitted with
    `business_meter()` in activities/API; queried in Grafana via SQL.
- **PULL (Prometheus scrape):** Temporal SDK operational metrics (`:9000`) + the Temporal
  Cloud OpenMetrics endpoint. On kind, an in-cluster Prometheus (2h hot) scrapes these,
  evaluates the `temporal_slot_utilization` recording rule, and `remote_write`s to a durable
  host **`prometheus-store`** (15d) that Grafana reads (ADR-0021). Workflow/activity custom
  metrics that must be replay-safe use `*.metric_meter()` and ride this pull path, NOT push.
- **Autoscaling:** KEDA is the single external-metrics provider (ADR-0023) — a Prometheus
  scaler on the steady queue (querying the recording rule) and a Temporal scaler with a
  composite guard for bursty/scale-to-zero queues. Not prometheus-adapter (they collide on
  the `external.metrics.k8s.io` APIService).
- **Grafana datasource choice:** Prometheus (`prometheus-kind` → `prometheus-store`) for
  operational/autoscale/PromQL; **ClickHouse (`clickhouse-logs`)** for business metrics +
  logs (SQL). One ClickHouse server backs both `otel_logs` and `otel_metrics_*`.

See ADR-0020/0021/0023/0024.

---

## Connection profiles

`appkit`'s connection-profile field-group (`TemporalConnectionSettings`) defines the
connection config; each app's `settings.py` composes it (with its own deltas). Local is the
default; Cloud is opt-in via env:

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

- **kind + Terraform + ArgoCD** (`deploy/`) — the supported, production-like lifecycle;
  workers (and the app tier) on k8s against Temporal Cloud (self-hosted-on-kind planned),
  GitOps delivery, worker versioning, autoscaling. Compose runs the host visibility/console
  plane alongside it. This is the end-to-end path.
- **`docker-compose.yml`** (`just legacy-up`) — a legacy, no-Kubernetes fallback: a self-hosted
  Temporal **server + app tier + LGTM**, with **no workers** (workers are a kind concern
  now). Boots a local server you can poke; it does not execute workflows end-to-end until
  OSS-on-kind lands.

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
