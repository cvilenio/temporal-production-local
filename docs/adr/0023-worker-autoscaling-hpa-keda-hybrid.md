# ADR-0023: Worker autoscaling — KEDA as single control plane, scaler per workload shape

- **Status:** Proposed
- **Date:** 2026-06-29
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
   prometheus-adapter — APIService collision.
