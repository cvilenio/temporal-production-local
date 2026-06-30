# 0025 — KEDA worker autoscaling (Phase 1: steady orders workers)

- **Status:** **Landed + verified live** (kind + Cloud path). Code static-green; live wiring
  confirmed end-to-end. No load test / forced scale event (deferred — needs its own Cloud budget).
- **Date:** 2026-06-30
- **ADRs:** ADR-0023 (now Accepted for Phase 1). Builds on ADR-0004 (Worker Versioning /
  controller-managed per-version Deployments), ADR-0021 (metrics pipeline), ADR-0025 (dep manifest).

## Done this session

- **KEDA pinned + wired into the delivery stack** as a platform add-on (the prometheus/cnpg
  precedent — `charts:` block only; not Tier-1/2 audited):
  - `config/dependencies.yaml` — `charts.keda` = **2.20.1** (appVersion 2.20.1), repo
    `https://kedacore.github.io/charts`.
  - `compose/scripts/render-deps.py` — exports `KEDA_VERSION` / `KEDA_REPO`.
  - `deploy/kind/mirror-deps.sh` — `helm pull keda` → local OCI registry.
  - No `applications.tf` chart-list edit needed: `chart_versions` is derived generically from the
    manifest and the add-on glob auto-discovers `keda.yaml`.
- **KEDA ArgoCD Application** `deploy/argocd/applications/keda.yaml` — sync-wave **-2** (CRDs +
  operator before the controller at -1 and workloads at 0), namespace `keda`, secret-free,
  `ServerSideApply=true` (KEDA CRDs are large). Sole external-metrics provider — **no**
  prometheus-adapter (APIService collision).
- **Worker-controller WRT allowlist** (`temporal-worker-controller.yaml`) extended
  `workerResourceTemplate.allowedResources` with `ScaledObject` (`keda.sh/scaledobjects`) beside the
  default HPA — gates the webhook + drives controller RBAC.
- **Per-worker autoscaling in the orders-workers chart (KEDA Temporal scaler, per-version):**
  - New `templates/workerresourcetemplate-scaledobject.yaml` — one `WorkerResourceTemplate` per
    worker (activity, workflow), each embedding a KEDA `ScaledObject` with `scaleTargetRef: {}`
    (controller injects the versioned Deployment) and a single **`type: temporal`** trigger with
    empty-string sentinels (`workerDeploymentName` / `workerDeploymentBuildId` / `namespace`) the
    controller fills **per running version** — so each Build ID scales on its OWN backlog
    (`DescribeWorkerDeploymentVersion`, `targetQueueSize: 5`). `minReplicaCount: 1` (no scale-to-zero
    yet). Plus one shared `TriggerAuthentication` (`apiKey` → `orders-cloud-apikey`, the Connection's
    secret); `endpoint` = `connection.hostPort`.
  - New top-level `autoscaling:` values block (default `enabled: false`), keyed by worker `name`
    — kept OUT of the TF-injected `workers:` array so it survives the cluster layer's override.
  - Cluster TF (`applications.tf`) injects `autoscaling = { enabled = true }` on the kind+Cloud path
    (deep-merges over the chart default), so host-OSS `helm template` renders nothing.
  - Chart bumped **0.1.8 → 0.1.9 → 0.1.10** (Chart.yaml + cluster `orders_workers_chart_version` in
    lockstep). 0.1.9 was the first cut (version-blind **Prometheus** scaler); 0.1.10 switched to the
    per-version **Temporal** scaler. Every template change needs a version bump (see Gotcha).

- **ADR-0023** amended: status Accepted (Phase 1); implementation-notes section records the KEDA
  Temporal-scaler-per-version decision, the **correction** that per-version metrics need no SDK bump
  (build_id is on the metrics via the controller's pod labels), auth, cost/signal tradeoffs, and the
  deferred scale-to-zero guard. The top-of-ADR Decision table carries a "revised in Phase 1" pointer.

## Verification (live, kind + Cloud)

- `just versions-audit` **35/35 green**; `helm lint` + `kubeconform` clean (chart renders 0 WRTs with
  autoscaling off, 2 WRTs + their ScaledObjects with it on).
- Console-first (`just up-cloud-kind` → `headlamp-reload` → `preflight` 200) then `just platform-up`.
- KEDA Healthy (operator + admission-webhooks + metrics-apiserver 1/1; CRDs `scaledobjects.keda.sh`
  et al. present). Worker-controller rolled cleanly with the new allowlist.
- **WRT → per-version ScaledObjects rendered, with per-version identity injected.** 2 WRTs → one
  `ScaledObject` per running Build ID, each a single `type: temporal` trigger + the shared
  `orders-temporal-apikey` TriggerAuthentication, all `Ready=True`. Confirmed each trigger carries a
  **distinct** injected `workerDeploymentBuildId` (each version's own Build ID hash),
  `workerDeploymentName=orders/orders-activity|orders-workflow`, `namespace=<temporal-namespace>`,
  `queueTypes` per worker. `scaleTargetRef: {}` injected to each versioned Deployment.
- **Scaler reaches Temporal Cloud.** Operator log: `Temporal scaler initialized … mode:
  deployment-version, authType: apiKey, unsafeSsl: false` per version; each version scales on its
  **own** backlog via `DescribeWorkerDeploymentVersion`. Idle → `Active=False`, replicas at floor
  (min 1). No scale event forced.
- **Registration-race note (expected, self-heals).** Right after a rollout, the freshly-created
  per-version ScaledObject briefly logs `Worker Deployment Version not found` because the controller
  renders it the instant the versioned Deployment appears — a few seconds before the worker registers
  that version with Temporal. It clears once the version goes `Current`/`Healthy` (verified: error
  gone after the new version registered). KEDA's min-1 floor holds during the window. The sunset/
  deregistration window is the symmetric case.
- **Cloud footprint:** 0 workflow executions started. The scaler issues read-only
  `DescribeWorkerDeploymentVersion` (Visibility API) polls; the controller rolled workers (new Build
  IDs) as reconcile — neither is a billable workflow execution.

## Gotcha recorded

- **ArgoCD caches OCI charts by version string.** First `platform-up` republished `orders-workers`
  0.1.8 with the new template, but ArgoCD served the **stale cached 0.1.8** (Synced/Healthy, yet no
  WRTs rendered). Fix = bump the chart version (0.1.9). Any change to a local chart's templates MUST
  bump `version:` (+ the cluster `orders_workers_chart_version` default) or ArgoCD won't re-pull.

## Open questions / next

- **Behavioral scale test still owed.** Proving an actual scale-up needs sustained backlog → many
  concurrent executions, which exceeds the 5-execution Cloud ceiling and fans out. Bring a bounded
  proposal (N orders on the activity queue, watch `kubectl get hpa -w`, terminate, confirm
  scale-down) for explicit approval before running.
- **Lingering versions are cosmetic churn.** Repeated `platform-up` runs left 3 build-ID versions
  per worker; the controller sunsets non-current ones (scaledownDelay 10m / deleteDelay 30m) and the
  WRT GCs their ScaledObjects/HPAs. Per ADR-0023 point 3, the HPA floor-1 keeps one idle pod per
  lingering version until deleteDelay retires it — expected, never strands pinned work.
- **Per-version scaling is DONE** (Temporal scaler, controller-injected Build ID) — earlier note
  claiming it needed a Core SDK bump was wrong (corrected in ADR-0023). temporalio 1.29 is latest
  stable; nothing pinned behind.
- **Leading per-version slot signal deferred.** The controller injects only into `type: temporal`
  triggers, and WRT can't template a Build ID into a Prometheus query — so a per-version
  slot-utilization trigger isn't expressible yet. Backlog (`targetQueueSize`) is the signal today.
- **Scale-to-zero (`minReplicaCount: 0`)** — next phase. Needs the per-version safe-to-zero guard
  (in-flight-task protection, kedacore/keda#7368) the deferred slot signal would provide.
