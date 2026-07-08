# ADR-0023: Worker autoscaling — KEDA as single control plane, scaler per workload shape

- **Status:** **Superseded (2026-07-02) by a custom direct-patch controller** — see
  "## Update (2026-07-02): superseded by temporal-worker-autoscaler" at the end. Phase 1 (per-version
  KEDA **Temporal scaler**) shipped and worked, but bottomed out on the HPA sync loop (too slow for a
  seconds-level posture) and tripped the Cloud per-API rate limit via per-version poll fan-out
  (steady non-zero `resource_exhausted_error_count`). The context/analysis below remains accurate and
  is why the custom controller exists; the KEDA seam has been retired.
- **Date:** 2026-06-29 (Phase 1 landed 2026-06-30; superseded 2026-07-02)
- **Related:** Builds on ADR-0004 (Worker Versioning via the Temporal Worker Controller) — the
  controller owns the per-version `Deployment` an autoscaler attaches to. Depends on the metrics
  enablement phase (worker SDK `/metrics` scrape + Cloud OpenMetrics endpoint) landing first; see
  checkpoint 0021. Connection/substrate switching follows ADR-0005 (local ↔ Cloud).

## Context

The repo runs workers at fixed replica counts (workflow 1, activity 2; ADR-0004). There is no
horizontal autoscaling. Two questions drove this ADR:

1. **Which external-metrics provider, and which scaler?** KEDA is **not a second autoscaler beside
   HPA** — a KEDA `ScaledObject` makes KEDA *generate* a managed HPA and serve its external metric.
   So "running KEDA" already means "running HPA"; KEDA adds only the **0↔1 activation**
   (scale-from-zero) that raw HPA lacks. Critically, KEDA and prometheus-adapter both own the
   cluster-wide `v1beta1.external.metrics.k8s.io` APIService and **cannot both serve it** — installing
   one displaces the other ([kedacore/keda#470](https://github.com/kedacore/keda/issues/470)). So the
   real choice is *which single external-metrics provider owns the cluster*, and — under KEDA — *which
   scaler feeds each workload* ([worker-controller scaling-recommendations](https://github.com/temporalio/temporal-worker-controller/blob/main/docs/scaling-recommendations.md)):
   - **Prometheus scaler** (or plain HPA + prometheus-adapter) — reads `temporal_slot_utilization`
     (a recording rule over worker-emitted slot metrics, leading) + backlog from a single
     namespace-wide Prometheus/OpenMetrics scrape.
     Fetch cost **O(1)** in (task queue × worker-deployment-version) units. **Floor ≥ 1** — the signal
     does not exist at zero replicas (no worker emitting; idle queue unloaded), so it cannot wake from
     zero.
   - **Temporal scaler** — calls `DescribeTaskQueue(stats=true)` / `DescribeWorkerDeploymentVersion`
     directly; the call **reloads an idle (unloaded) queue synchronously**, so it **scales from zero**.
     Cost **O(N) API calls** against the per-namespace Visibility rate limit
     (`FrontendGlobalWorkerDeploymentReadRPS = 50`; self-hosted `frontend.*namespaceRPS.visibility`,
     since server 1.24 `DescribeTaskQueue` is bound by visibility limits).

   Neither is universally better: O(1) fetch + always-warm floor vs. per-unit API cost + scale-to-zero.
   It is the *same fork* whether framed HPA-vs-KEDA or Prometheus-scaler-vs-Temporal-scaler.

2. **Which fits which workload?** Steady, continuously-loaded queues never benefit from scale-to-zero
   and would only pay the Temporal-scaler's API-poll cost; bursty queues with long idle windows and
   lax latency are exactly where scale-to-zero pays off. The choice is a property of the **workload
   shape**, not a global repo decision.

Cloud is the primary substrate (OSS supported). On Cloud the OpenMetrics endpoint exists, so the
Prometheus-scaler path is natural; the idle-queue-unload-after-5-min behavior is a Cloud behavior that
makes the no-scale-from-zero limitation bite harder for bursty work. The Temporal-scaler path is
**substrate-portable** — identical API on Cloud and OSS — whereas the Prometheus path's metric source
differs per substrate (Cloud OpenMetrics vs self-hosted server metrics).

## Decision

> **Revised in Phase 1 (see implementation notes).** The "Prometheus scaler for steady" row below was
> the original proposal; it is **version-blind** (the controller injects per-version identity only into
> `type: temporal` triggers), and per-version scaling is a hard requirement. Phase 1 therefore uses the
> **KEDA Temporal scaler for all workloads** (per-version backlog), `minReplicaCount: 1` for now,
> scale-to-zero deferred. The table is retained for the workload-shape reasoning; the scaler choice
> narrowed to Temporal-for-everything.

**Run KEDA as the single autoscaling control plane; choose the scaler per workload shape; demo both
behaviors in-repo on distinct task queues.** Do **not** run prometheus-adapter alongside KEDA
(external-metrics APIService collision) — "HPA-like steady" is expressed as a KEDA ScaledObject with
`minReplicaCount: 1`, not a second adapter stack. Plain HPA + prometheus-adapter (no KEDA) stays a
documented *alternative* for orgs that never need scale-from-zero and want O(1) simplicity at very
large scale — an alternative, **never an addition**.

| Workload shape | KEDA scaler | minReplicaCount | Signal |
|---|---|---|---|
| Steady / latency-sensitive | **Prometheus scaler** | **1** (always warm) | `temporal_slot_utilization` (leading) + backlog, from in-cluster Prometheus |
| Bursty / idle / lax-latency | **Temporal scaler** | **0** | per-version backlog via `DescribeWorkerDeploymentVersion`; `activationTargetQueueSize` for the 0→1 wake, guarded by the composite signal (point 4) |

1. **Two demo workload archetypes, separated by task queue** (consistent with the existing
   per-queue split, ADR-0004). The steady archetype is the existing orders flow on its own queue,
   autoscaled by the **KEDA Prometheus scaler at `minReplicaCount: 1`**. A **new bursty archetype**
   (its own workflow + activities on its own queue) demonstrates the **KEDA Temporal scaler scaling
   from zero**. Separate queues are what let each have its own independent ScaledObject. The concrete
   bursty workload is an open question (see below).

2. **Autoscaler attaches to the controller-managed, per-version `Deployment`.** The Temporal Worker
   Controller is *designed* to coexist with an external autoscaler per worker-deployment-version —
   this is a supported seam, not a `.spec.replicas` ownership fight. **KEDA per-version queries
   require worker-controller v1.8.0+** — the repo is on appVersion 1.7.0 today, so a bump is a
   prerequisite (see Prerequisites).

3. **Versioning interaction (ADR-0004 drain/PINNED).** During a rollout, old and new versions
   coexist as separate per-version Deployments:
   - **KEDA (scale-to-zero):** a `PINNED` in-flight workflow that needs the old version generates
     backlog on that version's queue → the next `DescribeTaskQueue` poll reloads the queue → KEDA
     **resurrects the drained-to-zero old version**. So scale-to-zero is safe with PINNED *as long
     as the wake signal is honored* — it does not permanently strand pinned work.
   - **HPA (floor 1):** the old version's Deployment stays at ≥1 replica until retired, so pinned
     work is always served — at the cost of one idle pod per lingering version. Never strands.

4. **Composite safe-to-zero signal (correctness, not just cost).** The native KEDA scaler (2.20)
   scales on **backlog only**, and has a known bug ([kedacore/keda#7368](https://github.com/kedacore/keda/issues/7368))
   where it scales workers to zero **while activities are still executing** — an empty backlog with
   a long-running activity looks "idle". The KEDA path in this repo MUST guard scale-to-zero with a
   composite signal — backlog **and** `slot_utilization` (recording rule over worker slot metrics —
   are slots busy now) **and** poller liveness (`LastAccessTime`) — so a version only goes to zero
   when all three agree it is idle.

5. **Rate-limit ceiling — a three-tier ladder, documented so the escape hatch is not cargo-culted.**
   KEDA's O(N) poll cost hits the per-namespace Visibility rate limit at scale (~1500 units saturates
   50 RPS at 30s polling). The ladder, cheapest first:

   | Tier | Move | When |
   |---|---|---|
   | 1 | Raise the Visibility API RPS limit (account team on Cloud; `frontend.*namespaceRPS.visibility` on OSS) | First resort |
   | 2 | Scaler-by-shape under one KEDA: **Prometheus scaler** (no API cost) on steady/hot queues, **Temporal scaler** only on the idle/bursty queues that truly need zero-wake; namespace-shard (limit is per-namespace) | Most production shops land here |
   | 3 | **Aggregating external scaler** (below) | The zero-wake (Temporal-scaler) subset *alone* is large, can't raise/shard enough |

   Tier 2 already confines Visibility-API pressure to the Temporal-scaler subset only — steady
   workloads on the Prometheus scaler cost zero Temporal API calls — which is why tier 3 is rare.

6. **Tier-3 aggregating external scaler (escape hatch).** A single component does rate-limited,
   batched `DescribeTaskQueue`/`DescribeWorkerDeploymentVersion` polls across the namespace, caches,
   and exposes a **KEDA external-scaler gRPC endpoint** (`GetMetricSpec`/`GetMetrics`/`IsActive` —
   `IsActive` is the scale-from-zero hook). This is the same architecture as `prometheus-adapter`
   (the recommended HPA path) — it decouples **reader fan-out** (many ScaledObjects) from **upstream
   API fan-out** (one rate-limited client), converting "KEDA blows past the limit" into "one client,
   graceful staleness as N grows". It does **not** remove the O(N) calls — it consolidates and
   self-governs them; staleness still scales with N at the extreme. It is also the natural home for
   the composite safe-to-zero signal (point 4). Requirements if built: **HA with leader election on
   the polling** (so HA does not double API load), liveness, and meta-monitoring (component down =
   fleet scales blind). **Build it as a removable layer** and track upstream (KEDA 2.x, controller
   v1.8.0, keda#7368) — retire it if the native scaler gains a composite signal + shared-connection
   aggregation. Note: because tier 2 already restricts API pressure to the zero-wake subset, the
   proxy's **rate-limit** rationale is narrow; its durable rationale is the **composite safe-to-zero
   signal** (point 4), which the native Temporal scaler cannot express regardless of scale.

## Consequences

- **Gain:** the repo demonstrates the full production decision space — steady (Prometheus scaler,
  floor 1) and bursty (Temporal scaler, scale-to-zero) — under **one** KEDA control plane, plus the
  documented escape hatch for the rate-limit ceiling. Matches the post-sale question a customer
  actually asks ("can I scale to zero without hitting a wall?").
- **Cost:** one control plane (KEDA) but two scaler configs to wire and operate; a second demo
  workload to build and keep meaningful; the Temporal-scaler path needs the composite-signal guard
  before it is safe to scale to zero.
- **Latency honesty:** the tier-3 proxy is a *scale* play, not a *latency* play — it adds a cache
  hop, so it is not lower-latency than a healthy native KEDA; it only wins relative to a throttled
  one. Cold-wake latency is dominated by the poll interval.

## Prerequisites (verified against the repo, 2026-06-29)

These gate the autoscaling phase; all are currently unmet:

1. **Metrics pipeline first.** No in-cluster Prometheus, no metrics-adapter, no KEDA exist today —
   greenfield. The Prometheus-scaler path and the composite safe-to-zero signal both consume scraped
   worker metrics; autoscaling is blind and unvalidatable without the pipeline. Lean **in-cluster
   Prometheus** on kind (the KEDA Prometheus scaler must query it), Alloy stays for logs.
2. **Worker-controller ≥ appVersion 1.8.0** for KEDA per-version queries. **Done** — bumped from
   chart 0.26.0 (appVersion 1.7.0) to **chart 0.27.0 (appVersion 1.8.0)** in `config/dependencies.yaml`
   (Terraform injects `targetRevision`). Deploy-time validation still owed: mirror the 0.27.0 chart
   into the local OCI registry (`just mirror-deps`) and confirm a worker rollout reconciles cleanly
   (CRD schema may have changed across the minor).
3. **`temporal_slot_utilization` is a Prometheus recording rule, not an SDK metric — no SDK bump.**
   The worker-controller demo computes it as a recording rule over the raw slot metrics the Core SDK
   already emits (`worker_task_slots_used`; `worker_task_slots_available` for fixed-size suppliers);
   the SDK metrics reference lists no `slot_utilization`. The pinned **temporalio 1.28.0** already
   emits the inputs, so the steady path's leading signal is a Prometheus rule to author during the
   metrics phase — its exact form depends on the slot-supplier type (fixed-size → `(max − available)/max`;
   resource-based → `worker_task_slots_used` has no static denominator).
4. **Install KEDA as the sole external-metrics provider** via the existing add-on pattern (ArgoCD
   Application YAML in `deploy/argocd/applications/`, version pinned in `config/dependencies.yaml`,
   chart mirrored into the local OCI registry per ADR-0013, sync-wave `-2`). Do **not** also install
   prometheus-adapter — APIService collision. **Done** — `deploy/argocd/applications/keda.yaml`
   (chart 2.20.1 / appVersion 2.20.1), pinned under `charts.keda`, mirrored by `mirror-deps.sh`.

## Phase 1 implementation notes (2026-06-30)

What Phase 1 wired, the design deltas found while building it, and one **correction** to an earlier
draft of these notes (verified against the `temporal-worker-controller` source at appVersion 1.8.0,
PR #324, the KEDA Temporal scaler docs, and the live cluster):

1. **The attach seam is `WorkerResourceTemplate` (WRT), and it templates a KEDA `ScaledObject`.**
   The controller renders one copy per running version, injects the versioned Deployment into
   `scaleTargetRef: {}` (recursive injection — valid for KEDA's `scaleTargetRef` too), and GCs each
   copy on sunset. `ScaledObject` was added to `workerResourceTemplate.allowedResources` (gates the
   webhook + drives controller RBAC).

2. **Use the KEDA *Temporal* scaler (`type: temporal`), NOT the Prometheus scaler — that is what
   makes scaling per-version.** The controller's `appendKEDATriggerMetadata` (appVersion 1.8.0+)
   injects per-version identity into trigger metadata **only for `type: temporal` triggers**:
   `workerDeploymentName`, `workerDeploymentBuildId`, `namespace` (opt-in via the empty-string
   sentinel). It does **not** inject into Prometheus triggers. So each version's ScaledObject reads
   its **own** backlog via `DescribeWorkerDeploymentVersion` → per-version, canary-/proportional-/
   N-versions-correct scaling. (A first cut used the Prometheus scaler at `minReplicaCount: 1`; it is
   version-blind — every version scales on one queue-wide series — and was replaced.)

3. **CORRECTION — per-version metrics do NOT require an SDK bump.** An earlier draft claimed
   temporalio 1.29 emits only a bare `namespace` scheme so per-version scaling was "blocked until a
   Core SDK bump." That is wrong: the worker pods carry `temporal.io/build-id` +
   `temporal.io/deployment-name` (set by the controller), surfaced onto every scraped series as
   `temporal_io_build_id` / `temporal_io_deployment_name` — confirmed live. And the Temporal scaler
   path doesn't use SDK metrics for versioning at all; it gets the Build ID injected by the
   controller. temporalio 1.29 is latest stable (verified) — nothing is pinned behind.

4. **Auth.** The Temporal scaler calls the Temporal frontend, so it needs a `TriggerAuthentication`
   (`apiKey`) — one shared, namespace-scoped resource pointing at the same `orders-cloud-apikey`
   Secret the Connection uses (the API key is per-namespace, not per-version). `endpoint` =
   `connection.hostPort`.

5. **Cost / signal tradeoffs (accepted).** The Temporal scaler makes O(versions × queues) Visibility
   API calls (the rate-limit ladder in this ADR covers extreme N). Signal today is **per-version
   backlog** (`targetQueueSize`); a *leading* per-version slot-utilization signal is deferred — the
   controller doesn't inject into Prometheus triggers and WRT can't template a Build ID into a query
   string, so a per-version slot trigger isn't expressible yet.

6. **Scale-to-zero deferred (next phase).** `minReplicaCount: 1` for now. The KEDA Temporal scaler
   supports `minReplicaCount: 0`, but its docs warn backlog activation "fails to account for in-flight
   tasks" (kedacore/keda#7368 — can zero a worker mid-activity). The safe-to-zero guard wants a
   per-version slot/poller signal (the deferred item in #5), so scale-to-zero waits for that phase.

---

## Update (2026-07-02): superseded by temporal-worker-autoscaler

**What changed.** The per-version KEDA **Temporal scaler** (Phase 1) is retired and replaced by a
small custom controller, `apps/platform/temporal-worker-autoscaler` (first Go deployable). The
`WorkerResourceTemplate → ScaledObject` seam and the `keda.sh/ScaledObject` entry in the
worker-controller `allowedResources` are removed; `orders-workers` now renders `WorkerAutoscaler`
CRs consumed by the controller.

**Why the KEDA/HPA path was insufficient here.** The driving requirement is a **production-like,
seconds-level scaling posture**. Both supported paths (Temporal's own
[`scaling-recommendations`](https://github.com/temporalio/temporal-worker-controller/blob/main/docs/scaling-recommendations.md),
PR #324, merged 2026-06-24) bottom out on the **cluster-wide HPA sync loop (~15s, not per-app
tunable, locked on managed clusters)** for 1→N — KEDA's external/temporal scalers feed that same HPA.
And they trade off: HPA+prometheus-adapter is rate-safe but ~3-min stale (Cloud OpenMetrics); the
KEDA Temporal scaler is fresh but rate-limited (`FrontendGlobalWorkerDeploymentReadRPS = 50`) — the
per-version poll fan-out is what lit the Resource Exhausted panel. **Neither is both fast and
low-API-load.** Consumer autoscaling is harder than request-serving autoscaling because the load
signal lives in the broker, not a request path; Knative KPA is fast only on the HTTP path and its
activator/buffering is redundant here (the Temporal task queue is already the durable buffer for
scale-to-zero). A community scan found no off-the-shelf fit (Custom Pod Autoscaler is the closest
own-loop/direct-patch pattern but is dormant).

**The decision.** A leader-elected singleton controller that (1) polls Temporal Cloud centrally for
fresh per-`(taskQueue, buildId)` backlog via `DescribeWorkerDeploymentVersion(ReportTaskQueueStats)`
— one caller, rate-limited + jittered, so O(task queues) and flat (ResourceExhausted → 0); (2)
computes desired replicas with a **mirrored Kubernetes HPA algorithm** (ratio + 10% tolerance
deadband + max-over-window downscale stabilization + step clamp) plus **Knative's stable/panic**
burst idea; (3) **patches the versioned Deployment `.spec.replicas` directly** for seconds-level
actuation (incl. scale-to-zero). Going custom **collapses** the Phase-1 fan-out problem: a single
controller is inherently the one central poller, so the aggregator/cache machinery a KEDA fan-out
would need disappears.

**Reuse vs build.** Reuse `controller-runtime` (scaffolding, Events, `/metrics`, leader election),
the Temporal Go SDK, and the *ideas* of the HPA algorithm + Knative stable/panic. Build ~60 lines of
decision math (do **not** import `k8s.io/kubernetes` — un-vendorable) with a swappable
`ScalingAlgorithm` interface (AIBrix shape). No PID (overkill; `go.einride.tech/pid` is the escape
hatch).

**Divergence, made defensible.** We diverge from HPA/KEDA in exactly **one** place — the actuation
loop (direct `scale` patch) — to remove the ~15s HPA-sync latency; everything else reuses proven
algorithms. It stays observable (Events on the Deployment + Prometheus + a rich `WorkerAutoscaler`
`.status`) and discoverable (Deployment annotations `autoscaler.ziggymart.io/*`, labels; **no
ownerReference on the Deployment** — GitOps/the Worker Controller own it), so scaling is never
"magic". CRD-driven + signal-source-agnostic (Temporal today; Kafka/SQS later).

**Kept vs retired.** Kept: the Temporal Worker Controller, Worker Versioning, the versioned
Deployments, `Connection`, the Cloud API-key Secret. Retired: the WRT→KEDA ScaledObject seam. KEDA
itself remains installed for now (harmless; removal is a follow-up).

**Customer-facing rule (the reusable lesson).** Reaction SLO looser than ~3–4 min → the supported
HPA+prometheus-adapter path on local `temporal_slot_utilization` (fast, per-version, zero Cloud
load) + stale Cloud backlog; **no divergence needed**. Tighter/seconds-level or scale-to-zero → a
custom direct-patch controller. Worker-sourced signals (slot utilization, schedule-to-start) are
local → poll as fast as you like; **backlog is server-side** → only fresh via live gRPC, rate-safe
only when centralized in one poller.

## Update (2026-07-08): slot utilization as a live second signal

The custom `temporal-worker-autoscaler` now optionally combines per-version task-queue backlog
(primary, live Cloud `DescribeWorkerDeploymentVersion`) with in-cluster Prometheus slot
utilization (`temporal_slot_utilization:by_build` recording rule) as a **secondary** signal:

- **Scale up: OR.** Backlog-driven scale-up OR slot saturation at the current replica count
  (catches sustained no-headroom before backlog grows).
- **Scale down: AND.** Shrink only when backlog is low **and** slots are idle (avg utilization
  below `scaleDownSlotUtilizationPercent`). Busy slots veto scale-down to protect in-flight work.

This is the expressiveness stock multi-metric HPA cannot provide (HPA takes the max desired across
metrics - OR-up only, no AND-down). Slot hints are fail-open: missing Prometheus data falls back to
backlog-only. **Scale-to-zero remains deferred** - the down-gate is the first input of the composite
safe-to-zero guard (backlog zero AND slots idle AND pollers alive); `minReplicas` defaults stay at
1 on orders workers until that guard ships.
