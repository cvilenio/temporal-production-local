# Checkpoint 0028 — Custom worker autoscaler (seconds-level, direct-patch)

**Date:** 2026-07-02
**Status:** **Landed + verified live** (kind + Cloud) — see "Live validation results" below.
KEDA fully removed. Merged to `main` via PR #26 (chart 0.1.2).
**Supersedes:** the KEDA autoscaling seam from checkpoint 0025 / ADR-0023 Phase 1.

## Why

The **Resource Exhausted Errors** panel (checkpoint 0027, Critical Flows → Workflow Progress) showed
steady non-zero `temporal_cloud_v1_resource_exhausted_error_count`. Traced (live Prometheus + kind)
to the **KEDA Temporal scaler**: one ScaledObject per worker version, each independently polling
`DescribeWorkerDeployment(Version)` on Cloud → synchronized bursts tripping a low per-API
control-plane limit. Not the task-pollers (zero throttling), not Prometheus scraping (separate plane).

Fixing it surfaced a deeper requirement: a **production-like, seconds-level scaling posture** that
KEDA/HPA structurally can't meet. Temporal's own authoritative doc
([temporal-worker-controller PR #324](https://github.com/temporalio/temporal-worker-controller/pull/324),
merged 2026-06-24) confirms the two supported paths — HPA+prometheus-adapter (rate-safe, ~3-min
stale) and the KEDA Temporal scaler (fresh, rate-limited) — and that **neither is both fast and
low-API-load**. Consumer autoscaling is harder than request-serving (the load signal lives in the
broker, not a request path); Knative KPA needs the HTTP path and its buffering is redundant here
(the task queue is the durable buffer). A community scan found no off-the-shelf fit (Custom Pod
Autoscaler is the closest own-loop/direct-patch pattern but dormant). Full reasoning: ADR-0023
"Update (2026-07-02)".

## What landed

**New Go app** (first Go deployable) `apps/platform/temporal-worker-autoscaler/go/` — a
leader-elected `controller-runtime` singleton:

- `internal/temporal` — the one Cloud call: `DescribeWorkerDeploymentVersion(ReportTaskQueueStats)`
  (the KEDA-proven call for Worker-Deployment versioning) → fresh per-version backlog. One caller,
  rate-limited + jittered → O(task queues), flat, un-bursty (ResourceExhausted → 0).
- `internal/scaling` — mirrored k8s HPA math (ratio + 10% tolerance deadband + max-over-window
  downscale stabilization + step clamp) + Knative stable/panic burst mode. Swappable
  `ScalingAlgorithm` interface (AIBrix shape). **9 unit tests.** Does NOT import `k8s.io/kubernetes`
  (un-vendorable) — algorithm copied.
- `internal/controller` — reconciler: discovers per-version Deployments by the
  `temporal.io/deployment-name` label → reads backlog → decides → **patches `.spec.replicas`
  directly** (seconds-level; incl. scale-to-zero) → Events on the Deployment + Prometheus + CRD
  status + annotations. `MaxConcurrentReconciles=1` (one serial Cloud caller). No ownerReference on
  the Deployment.
- CRD `WorkerAutoscaler` (`autoscaling.ziggymart.io/v1alpha1`): spec (deploymentName, taskQueue,
  queueType, min/max, targetBacklogPerReplica, optional behavior) + rich status (per-version
  breakdown, conditions, lastScaleTime, reason).

**Packaging:**
- `images/go.Dockerfile` (configurable per Go app) + `just build-images/push-images/image-digests`
  + `chart-publish` + `platform-up` digest threading now include the controller.
- `deploy/charts/temporal-worker-autoscaler/` — Deployment, ClusterRole/binding, leader-election
  Role, SA, CRD. Renders + lints clean.
- `orders-workers` chart (v0.1.11 → **0.1.12**): WRT→KEDA ScaledObject template **removed**; new
  `templates/workerautoscaler.yaml` renders one `WorkerAutoscaler` CR per worker; `autoscaling:`
  values reshaped (queueType, targetBacklogPerReplica, optional behavior).
- `deploy/argocd/applications/temporal-worker-controller.yaml`: `keda.sh/ScaledObject` removed from
  `allowedResources` (WRT seam unused).
- TF cluster layer: `temporal_worker_autoscaler_application` (ArgoCD app, injected
  connection + image), `autoscaler_chart_version` / `autoscaler_image_tag` / `autoscaler_image_digest`
  vars, image added to the `:latest` safety precondition, `orders_workers_chart_version` → 0.1.12.
  `terraform validate` passes.

**Kept untouched:** Temporal Worker Controller, Worker Versioning, versioned Deployments,
`Connection`, the `orders-cloud-apikey` Secret. KEDA install remains (harmless; removal is a
follow-up).

## Go app layout (first in the repo; ADR-0022 was Python-only)

`apps/platform/<app>/go/` mirrors the Python `settings/wiring/main` split conceptually:
`internal/config` (env→Config), `internal/{temporal,scaling,metrics}` (wiring/domain),
`internal/controller` + `cmd/main.go` (composition root). `make all` regenerates
deepcopy/CRD/RBAC (controller-gen) + fmt/vet/build/test. The chart's `crds/` copy of the CRD must be
re-synced from `config/crd/bases/` when the API types change.

## Defensibility (the "HPA or KEDA?" answer)

Neither — deliberately. Seconds-level actuation is impossible on HPA/KEDA (both bottom out on the
cluster-wide ~15s HPA sync loop). We reuse k8s HPA math + Knative's burst model and diverge in
exactly one place — the actuation loop (direct `scale` patch) — kept observable + discoverable so it
is never "magic", and CRD-driven + signal-source-agnostic so it is reusable.

## Verification plan (remaining — needs a human present)

Console up first (`just up-cloud-kind`; `just preflight`). Then `just platform-up` (builds + pushes
the controller image, publishes charts, applies TF, ArgoCD syncs). Confirm:

1. `sum(temporal_cloud_v1_resource_exhausted_error_count)` → 0; KEDA `DescribeWorkerDeployment*` rate
   → 0; only the controller's flat, jittered `DescribeWorkerDeploymentVersion` remains.
2. **Seconds-level scale-up + no flap:** drive a backlog on `orders-workflow-task-queue` (respect the
   CLAUDE.md live-Cloud ceiling — ≤5 executions or ask). Replicas rise within the poll interval
   (seconds, not the ~15s HPA floor); after drain, decay per the max-over-window stabilization with
   no oscillation (`kubectl get deploy -w`).
3. **Observability/discoverability:** `kubectl describe deployment` shows scale Events;
   `kubectl describe workerautoscaler` shows status/conditions/reason; Deployment annotations present;
   `temporal_worker_autoscaler_*` Prometheus series populated.
4. **Per-version correctness** during a rollout: each version scales on its own backlog (drainers at
   min, current scales).

## Open items

- **Commit/PR** the change (currently uncommitted working tree).
- **Draining-version min floor:** the controller floors *every* discovered version at `minReplicas`,
  so a draining old version gets pulled to min rather than 0 — refine to let non-current versions
  drain (tie into the safe-to-zero guard).
- **Scale-to-zero** supported but left at `minReplicas: 1` pending a safe-to-zero guard for in-flight
  tasks (same concern ADR-0023 raised).
- **Reproducible worker builds** to stop per-`platform-up` version churn (each new image digest = a
  new version the worker-controller must poll, consuming the shared Worker-Deployment-Read limit).
- A Grafana panel for the new `temporal_worker_autoscaler_*` metrics.
- Add `go build/test` for the new module to the `just check`/CI gate.
- Empty `keda` namespace lingers after operator prune (cosmetic) — `kubectl delete ns keda` to tidy.

## Live validation results (2026-07-02, deployed to kind + Cloud)

Deployed via `just platform-up`; validated end-to-end:

- **Per-version discovery + decisions:** `WorkerAutoscaler` status shows each build-id
  version with its own backlog/current/desired (e.g. workflow `1a2e…`, `1b7b…`, draining
  `779735…`). Correct: `backlog=0 → min`.
- **Seconds-level actuation:** bumping `orders-workflow` `minReplicas 1→3` (with ArgoCD
  selfHeal paused) → controller patched the current-version Deployment **1→3 in ~4s**, with an
  Event on the Deployment (`scaled … from 1 to 3 (backlog=0 target=5 → 3)`) and a
  `autoscaler.ziggymart.io/last-scale-reason` annotation. Well under the ~15s HPA-sync floor.
- **Damped no-flap scale-down:** reverting `minReplicas→1` held the Deployment at 3 across the
  observation window (max-over-window down-stabilization) rather than snapping down.
- **Rate-safety:** with KEDA removed + `pollInterval` 3s→**15s** + a spaced (burst-1) limiter,
  the controller logs **zero** rate-limit errors. **Zero Cloud workflow executions** were used
  (actuation proven via the min-bump, not load).
- **KEDA fully removed:** ScaledObjects, WorkerResourceTemplates, TriggerAuthentications, the
  KEDA operator (argocd app + namespace pods), and all active config/deps/mirror/render/headlamp
  references are gone. Worker Controller + Worker Versioning untouched.

### Fixes made during validation
- `pollInterval` default 3s → **15s** and the describe limiter → spaced burst-1: the Cloud
  Worker-Deployment-Read API trips at a low RPS, so 3s across multiple versions was too aggressive.
- Graceful error handling: `NotFound` (draining/just-registering version) → treated as backlog 0;
  `ResourceExhausted` → soft `ErrRateLimited` (hold replicas, log at V(1), no error-spam).

## Post-landing fix (2026-07-02) — draining-version replica fight

The "draining-version min floor" open item was not cosmetic: it caused an **active
two-controller fight**. The reconciler floored *every* discovered version at
`minReplicas` (backlog=0 → 1), while the temporal-worker-controller drives drained
versions → 0 to GC them. Result: the versioned Deployment ping-ponged (autoscaler patches
1, controller patches 0) every poll → constant pod create/kill (the "0↔1 thrash" seen in
Headlamp), one drainer stuck `ErrImagePull` (its digest was GC'd from the local registry by
version churn), and — because drained versions never *stayed* at 0 — the worker-controller
could never delete them, so 6 live versions persisted and kept getting polled on the shared
Worker-Deployment-Read limit → **ResourceExhausted stayed elevated**. Both reported symptoms,
one root cause.

**Fix (chart 0.1.0 → 0.1.1 → 0.1.2, new image):**
- The reconciler reads the Worker Controller's `WorkerDeployment` CR
  (`temporal.io/v1alpha1`, in-cluster — no extra Cloud call) and skips **only fully-Drained**
  versions (`status=Drained` / `eligibleForDeletion`) — the ones the worker-controller drives
  to 0 and deletes. **Current, Ramping, AND still-Draining versions are all managed**, each
  scaled on its own backlog. This is the key correctness point: *draining ≠ drained* — a
  Draining version has no new workflows routed to it, but its already-started **pinned**
  workflows keep generating tasks, so it has real backlog and must keep scaling (critical for
  proportional scaling across side-by-side versions during a canary / rainbow rollout).
  Fallback: if the CR is absent (no Worker Controller), manage every version.
  (0.1.1 first shipped the too-blunt "manage only current + ramping", which skipped Draining
  too and would have lost scaling on old versions still doing pinned work — corrected in 0.1.2
  after review.)
- New RBAC: `temporal.io/workerdeployments` get/list/watch (marker + chart ClusterRole +
  `config/rbac/role.yaml`).
- Chart `pollInterval` default 3s → **15s** (the code default was already 15s, but the chart
  env override forced 3s onto the running pod — that was why the deployed controller still
  polled every 3s).

**Routing vs scaling (Temporal best practice, confirmed via docs):** traffic routing across
versions is *server-side* — the Worker Deployment `RoutingConfig` (one Current + one Ramping +
a ramp % [0,100]); it is two-way at any instant, not an arbitrary N-way weighted split. True
rainbow (>2 live versions) comes from **pinned workflows** draining on old versions, not from
an N-way splitter. The temporal-worker-controller owns routing declaratively (`rollout:
Progressive` steps); this autoscaler must NOT do routing — it only reacts to the per-version
backlog that routing + pinned load produce. The two planes compose through the per-version
backlog signal (Temporal added per-version labels on `approximate_backlog_count` for exactly
this build-id-aware scaling).

**Verified live (kind + Cloud):** new pod runs `pollInterval:15`; thrash gone (all worker
pods Running, zero create/kill churn); `WorkerAutoscaler` status corrected to CURRENT=1
DESIRED=1 (was stale 3); 2 of 4 drained versions GC'd immediately, the other 2 sit stable at
0/0 awaiting the worker-controller's GC cadence (no longer resurrected). ResourceExhausted:
autoscaler's `DescribeWorkerDeploymentVersion` fell to a flat 0.1/s; the residual is
dominated by the worker-controller's own `DescribeWorkerDeployment` reconciliation
(~0.48/s, decaying as versions GC) — its cost scales with live-version count, so the durable
lever is reproducible worker builds (below), not the autoscaler.

### Operational learnings (important)
- **The Worker-Deployment-Read API rate limit is SHARED with the temporal-worker-controller**,
  which polls `DescribeWorkerDeployment` to reconcile versions. Its consumption scales with the
  number of live worker versions. So the autoscaler must stay gentle, and **version churn is
  expensive** — every image rebuild with a new digest = a new version the controller must poll.
  (This repo's non-reproducible worker builds churn a version per `platform-up`; the residual
  ResourceExhausted seen right after this migration is that churn draining, not the autoscaler.)
- **Migration ordering matters:** remove the WorkerResourceTemplates BEFORE stripping the
  worker-controller's `keda.sh/ScaledObject` from `allowedResources` — otherwise the controller
  loses the RBAC to delete the WRTs' ScaledObject children and the validating webhook blocks WRT
  deletion (a stalemate we hit and recovered from).
