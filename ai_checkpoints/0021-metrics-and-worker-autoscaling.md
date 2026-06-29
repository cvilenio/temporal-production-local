# 0021 — Metrics enablement + worker autoscaling (HPA/KEDA hybrid)

- **Status:** Design landed; one prerequisite bump executed (worker-controller chart 0.26.0 →
  0.27.0). Two short-term goals scoped and sequenced; ADR-0023 written (Proposed). Metrics +
  autoscaling implementation not started.
- **Date:** 2026-06-29
- **ADRs:** **ADR-0023** (new, Proposed) — worker autoscaling strategy. Builds on ADR-0004 (worker
  versioning controller) and ADR-0005 (connection profiles).

## Context — two goals on the short-term radar

1. **Metrics:** logging is implemented (ADR-0018/0020) but there is no push/pull metrics path
   proven end-to-end. Workers already *emit* SDK metrics (`appkit/telemetry.py` binds
   `PrometheusConfig` `/metrics` on :9000) but **nothing scrapes them on kind**, and the Cloud
   OpenMetrics endpoint (`metrics.temporal.io`) is not integrated.
2. **Autoscaling:** no HPA/KEDA; fixed replicas (workflow 1, activity 2). Must interact correctly
   with the worker versioning controller (ADR-0004).

## Decisions

- **Sequence: metrics first, then autoscaling.** Hard dependency, not just tidy order — the correct
  scale signal (`slot_utilization` / backlog) *is* a metric, and the composite safe-to-zero guard
  needs scraped metrics. Autoscaling is blind and unvalidatable without the pipeline. CPU-based HPA
  is the wrong lever (workers are IO-bound on pollers; CPU stays low while backlog grows).
- **Autoscaling = KEDA as the single control plane** (ADR-0023), scaler chosen per workload shape.
  KEDA *is* an HPA factory (a ScaledObject generates a managed HPA + serves its external metric);
  KEDA and prometheus-adapter collide on the `external.metrics.k8s.io` APIService, so you run **one**
  provider, not both. Steady/latency-sensitive → **KEDA Prometheus scaler, `minReplicaCount: 1`**
  (this *is* HPA-floor-1 behavior); bursty/idle/lax-latency → **KEDA Temporal scaler, scale-to-zero**.
  Plain HPA+prometheus-adapter (no KEDA) is an *alternative*, never a co-installed addition. Demo
  **both behaviors** in-repo on distinct task queues.
- **Temporal-scaler scale-to-zero must use a composite guard** (backlog + slot_utilization + poller
  liveness) — native KEDA 2.20 is backlog-only and has a known kill-busy-workers bug
  (kedacore/keda#7368).
- **Rate-limit ceiling has a 3-tier ladder** (ADR-0023 §5): raise Visibility RPS → scaler-by-shape
  under one KEDA (Prometheus scaler costs no Temporal API calls, so only the zero-wake subset hits
  the limit) + namespace-shard → tier-3 **aggregating external scaler** (rate-limited shared-connection
  poller behind a KEDA external-scaler gRPC endpoint; same shape as prometheus-adapter; HA +
  leader-election; removable; durable rationale is the composite signal, not the rate limit).

## Open questions

- **Bursty demo workload:** user will provide the use case later. Second archetype = its own
  workflow + activities + task queue, idle with bursts, scale-to-zero-tolerant. Until then, kind must
  be **wired to support both paths** (KEDA installed, in-cluster Prometheus, both scaler types
  exercisable) even before the concrete bursty workload exists.

## Verified (2026-06-29) — all prerequisites currently unmet

- **No autoscaling/metrics stack exists** — greenfield. No KEDA, no prometheus-adapter, no in-cluster
  Prometheus. Add-on pattern: ArgoCD Application YAML in `deploy/argocd/applications/` + version pin
  in `config/dependencies.yaml` + chart mirrored to local OCI (ADR-0013), sync-wave `-2`.
- **Worker-controller bumped:** chart **0.26.0 → 0.27.0** (appVersion 1.7.0 → **1.8.0**, the KEDA
  per-version floor) in `config/dependencies.yaml`. Deploy-time validation still owed: mirror 0.27.0
  into local OCI + confirm a worker rollout reconciles (CRD schema may differ across the minor).
- **`slot_utilization` needs NO SDK bump (corrected).** It is a **Prometheus recording rule** over
  the slot metrics the Core SDK already emits (`worker_task_slots_used`; `worker_task_slots_available`
  for fixed-size suppliers) — the worker-controller demo defines it this way; the SDK metrics
  reference lists no `slot_utilization`. Pinned **temporalio 1.28.0** already emits the inputs, so
  this is a recording rule to author in the metrics phase, not a dependency bump.
- **kind scrape approach decided:** lean **in-cluster Prometheus** (the KEDA Prometheus scaler must
  query it; it also hosts the slot_utilization recording rule). Alloy stays for logs (ADR-0014/0020).

## Next

1. **Metrics phase:** stand up in-cluster Prometheus (sync-wave `-2`), scrape worker + orders-api
   :9000, author the `slot_utilization` recording rule over the emitted slot metrics, add the Cloud
   OpenMetrics scrape (API-key SA "Metrics Read-Only", 30s, never `rate()`), stand up worker-health
   signals/alerts (schedule-to-start p99, sync-match, slots-available, sticky-eviction, backlog).
   Validate at destination, not source.
2. **Unblock autoscaling:** worker-controller bump done (mirror + deploy-validate when the metrics
   phase lands); install KEDA as the sole external-metrics provider (not prometheus-adapter).
3. **Autoscaling phase (after metrics proven):** wire KEDA Prometheus scaler (`minReplicaCount: 1`)
   on the steady orders queue; build the bursty demo workload + KEDA Temporal scaler (scale-to-zero,
   composite guard) on its queue; validate scaling actions against the Phase-1 metrics.
4. **Promote ADR-0023 to Accepted** once both behaviors are demonstrated; wire a line into
   `docs/ARCHITECTURE.md`.
