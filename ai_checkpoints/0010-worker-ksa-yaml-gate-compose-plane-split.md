# 0010 — worker KSAs, YAML validation gate, Compose plane split, rollout cold-start fix

- **Status:** **LANDED (2026-06-24).** Committed (this checkpoint's commit). kind+Cloud
  validated live.
- **Date:** 2026-06-24
- **ADRs:** none new. Updated **ADR-0004** (rollout strategy `Manual` → `AllAtOnce` + the
  manual-promotion ownership caveat) and **ADR-0008** (new "Kubernetes pod identity" section:
  named KSAs now, Workload Identity Federation deliberately deferred).

## Why

Three cleanup items spotted after 0009, plus two findings that surfaced while validating the
work live on the kind + Cloud run-mode:

1. Worker pods on kind ran under the namespace **`default` ServiceAccount** with the token
   auto-mounted — no permission isolation.
2. A growing pile of YAML (20 files) had **no linting / schema validation** — only Python
   (ruff/pyright) and ad-hoc `terraform fmt` / `docker compose config` checks existed.
3. The Compose env still encoded the **original Docker-only topology**: the base file mixed
   the host visibility plane, the app tier, and the worker tier, and the worker fleet was
   duplicated against the kind paths (two fleets polling the same Cloud task queues).

## Done this session

- **Item 1 — named KSAs (`deploy/charts/orders-workers`).** New `templates/serviceaccount.yaml`:
  one KSA per worker profile (`orders-workflow`, `orders-activity`), `automountServiceAccountToken:
  false`. `templates/workerdeployment.yaml` sets `serviceAccountName` + token-off on the pod
  template. Verified the pinned worker-controller **v1.7.0** CRD podSpec carries both fields
  before relying on them. WIF documented as the prod-GKE overlay this air-gap-local env omits
  (ADR-0008) — Temporal Cloud auth is API-key, not cloud-IAM, and the local registry is zot/
  plain-http, so WIF has no faithful local target.
- **Item 2 — manifest validation gate (`deploy/lint-manifests.sh`).** `helm lint` + `kubeconform`
  (k8s schemas + ArgoCD `Application` and Temporal `WorkerDeployment`/`Connection` CRD schemas
  from the datreeio catalog). Soft-skips kubeconform when absent (matches the pre-commit hook's
  `claude`-CLI pattern). Wired into `poe lint` → flows through `just check` / `just ci`. Left
  the secret-scan pre-commit hook single-purpose (didn't bolt manifest lint onto it — avoids
  forcing helm+kubeconform on every commit). The gate immediately caught a real bug: the chart's
  committed `connection.hostPort` placeholder `<regional-endpoint>:7233` violated the CRD regex;
  fixed to the schema-valid `regional-endpoint.example:7233`.
- **Item 3 — Compose plane split.** New `compose/workers.yml` (worker tier as an opt-in layer);
  the base `docker-compose.yml` is now the host platform/visibility plane only (no workers).
  poe: `up` / `up-cloud` / `up-cloud-prod` include `workers.yml`; **new `up-cloud-kind`** = base
  only (kind owns the workers), with a `just up-cloud-kind` recipe and updated `platform-up` hint.
  `down-cloud` covers both Cloud variants. Docs: RUNMODES task table + Files. Kills the duplicate
  fleet on the kind paths.
- **Rollout cold-start fix (`values.yaml` + ADR-0004).** Changed `rollout.strategy` `Manual` →
  `AllAtOnce`. `Manual` registers every version `Inactive` and needs a human
  `set-current-version` for *each* version including the first, so a fresh cluster has no Current
  version and versioned workflows sit pending. `AllAtOnce` auto-promotes the first healthy
  version. Documented the ownership caveat (a manual `set-current-version` flips
  `LastModifierIdentity` off the controller, which then backs off; hand control back via
  `temporal.io/ignore-last-modifier: true` metadata or delete+redeploy the Worker Deployment).

## Verification

- **Static:** `helm lint` clean; `kubeconform` 5/5 (chart: 2 KSA + 2 WorkerDeployment + 1
  Connection) and 5/5 (4 ArgoCD apps + registry ConfigMap), `-strict`; ran inside the real
  `just ci` lint gate (ruff/pyright/pytest also green). `docker compose config -q` valid for all
  three `-f` combos; worker tier present on the Compose paths and **absent (0 services)** on the
  kind path.
- **Live kind + Cloud (`just platform-up`, exit 0):** 11 TF resources, 4 ArgoCD apps
  Synced/Healthy. Worker pods: 3 Running+Ready under **named KSAs** (`orders-workflow`,
  `orders-activity`), `automount=false`, **no SA token volume** mounted. Host tier via
  `up-cloud-kind` brought up the app tier + visibility with **zero worker containers**.
- **Order E2E:** `POST /submit-order` → workflow started on Cloud; a **kind worker executed the
  workflow task** (proving KSAs + plane split don't break execution). Order did not *complete* —
  see findings below.
- **Bonus fix (not committed; `.venv` is gitignored):** the venv had stale shebangs pointing at
  the pre-rename dir `temporal-platform-demo`, breaking `uv run poe`. Rebuilt via
  `rm -rf .venv && uv sync`. Other clones/machines may hit the same.

## Findings from the live run (both PRE-EXISTING, not regressions)

1. **Cold-start promotion trap — FIXED** (rollout `AllAtOnce`, above). The deployments had never
   had a Current version, so an order was likely never completed E2E on kind+Cloud before. A
   manual `set-current-version` was run once during validation to unblock the test order — which
   means the two deployments are currently in controller-backoff (manual ownership); a clean
   redeploy or the `ignore-last-modifier` metadata returns them to the controller.
2. **Cross-plane connectivity — DECIDED, not yet built.** The order's activity failed with
   `All connection attempts failed` calling `orders-service`: kind workers can't reach the app
   tier (orders-api, mock-api) on the host via Compose DNS. This is the half-state (workers on
   kind, app services on host). **Decision taken (see Next).**

## Decision recorded — app tier topology (to build next)

- **orders-api + orders-db → kind, the existing `orders` namespace.** orders-db via **CNPG**
  (matches the `temporal-server` chart's Postgres pattern). orders-api is the system-of-record
  AND the Temporal client that starts workflows. Co-locate with the workers (one cohesive
  domain ns; ns already holds the WorkerDeployments + Cloud apikey Secret).
- **mock-api → stays on the host** as the *simulated external dependency* ("the internet").
  Workers reach it via `host.docker.internal` — which now correctly models cluster egress, not
  a workaround.

## Next — build "app tier onto kind"

- New chart (e.g. `deploy/charts/orders-app` or extend orders-workers): orders-api Deployment +
  Service, CNPG `Cluster` for orders-db, the app's Cloud apikey Secret wiring.
- ArgoCD Application + sync-wave (after the worker-controller, alongside/after the workers).
- TF cloud-namespace: mint an **orders-api client service account + API key** (it starts/signals
  workflows — decide least-privilege vs reusing the worker SA).
- orders-workers chart: inject **`MOCK_API_URL=http://host.docker.internal:8001`** into the
  WorkerDeployment env (the controller injects only `TEMPORAL_*`; reuse the existing conditional-
  env mechanism that adds `TEMPORAL_TLS`) and ensure kind nodes have host-gateway reachability.
  Note: `ORDERS_SERVICE_URL` needs NO override — co-locating in `orders` ns means the workers'
  default `http://orders-service:8000` resolves in-cluster to the new Service.
- `up-cloud-kind`: drop orders-api + orders-db from the host set (now on kind); keep mock-api +
  console + visibility.
- **Console:** orders-api log streaming moves docker-socket → k8s, so this pulls in **ADR-0015
  phase-2** (kube_status provider). Sequence with the move.
- Re-validate E2E: order completes on kind+Cloud (auto-promoted version, in-cluster orders-api,
  host mock-api via host.docker.internal).

### Carried over from 0009 (still open)

- ADR-0015 phase 2 (`kube_status` provider → live architecture page on kind) — now coupled to
  the app-tier move above. Phase 3: topology-as-data for multi-domain.
- Wire observability onto kind (chart + scrape) — currently proven only on Compose-OSS.
- `kind + Local OSS` run-mode row (in-cluster `charts/temporal-server` backend).
