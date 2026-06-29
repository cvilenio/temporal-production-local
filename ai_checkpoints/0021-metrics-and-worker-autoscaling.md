# 0021 — Metrics enablement + worker autoscaling (HPA/KEDA hybrid)

- **Status:** **Metrics phase shipped and deploy-validated end-to-end** (PRs #11, #13–#16 merged to
  `main`, applied to the live kind+Cloud stack). Autoscaling (KEDA) not started but unblocked — its
  scale signal is proven flowing. ADR-0023 still Proposed.
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
  the slot metrics the Core SDK already emits — the worker-controller demo defines it this way; the
  SDK metrics reference lists no `slot_utilization`. Pinned **temporalio 1.28.0** already emits the
  inputs, so this is a recording rule to author in the metrics phase, not a dependency bump.
- **kind scrape approach decided:** lean **in-cluster Prometheus** (the KEDA Prometheus scaler must
  query it; it also hosts the slot_utilization recording rule). Alloy stays for logs (ADR-0014/0020).

## Done (2026-06-29) — metrics phase, shipped + validated

The topology: **scrape inside kind, persist outside it** — the metrics analog of the Alloy→ClickHouse
log path. In-cluster Prometheus (short 2h retention) scrapes SDK `:9000` + the Cloud OpenMetrics
endpoint, evaluates the recording rule KEDA reads, and `remote_write`s everything to a host Prometheus
(`prometheus-store`, 15d) that Grafana reads. KEDA needs a local query target and logs have no
in-cluster reader, so the short in-cluster hot buffer is the only divergence from the log path.

- **In-cluster Prometheus** (PR #11): `prometheus-community/prometheus` 29.13.0 pinned in
  `config/dependencies.yaml`, mirrored to local OCI, ArgoCD Application sync-wave `-2`, secret-free.
  Lean (no Alertmanager/pushgateway/kube-state/node-exporter). SDK scrape via the chart's default
  `kubernetes-pods` job + `prometheus.io/scrape|port|path` pod annotations on the worker + orders-api
  templates. **No NodePort** — KEDA + the rule read it over cluster DNS; Grafana reads the host store.
- **Host store** (PR #11): docker-compose `prometheus-store` (remote-write receiver, 15d) + a Grafana
  "Prometheus (kind metrics)" datasource. Distinct from lgtm's bundled Prometheus (the Local-OSS store).
- **Cloud OpenMetrics scrape:** `metrics.temporal.io` `/v1/metrics`, 30s, `honor_timestamps`,
  **never `rate()`** the `temporal_cloud_v1_*` series. API key from a **`metricsread`** service account.
- **Cloud SA now native** (PR #13): bumped the `temporalcloud` provider `~> 0.9 → ~> 1.5` (cloud layer
  **and** the cloud-namespace module — both re-declare the pin). 0.9.2's `account_access` couldn't
  express `metricsread`; the module already validated it. `metrics-reader.tf` mints it in-band;
  cluster layer reads the token via remote state → `cloud-metrics-apikey` Secret (out-of-band `tcld`
  var remains a fallback). **Plan was additive-only: `2 to add, 0 change, 0 destroy`** — the major
  provider jump touched zero existing namespaces/SAs.
- **Chart-version discipline** (PR #14): annotation edits to the orders-workers/orders-api templates
  did nothing until the Chart.yaml versions were bumped (0.1.6→0.1.7 / 0.1.1→0.1.2) — ArgoCD treats
  published OCI chart versions as immutable revisions.

### Metric-name / label findings (ground truth for the autoscaling phase)

- **SDK metrics carry the `temporal_` prefix.** The slot gauges are `temporal_worker_task_slots_used`
  / `temporal_worker_task_slots_available`, NOT the unprefixed names this checkpoint first assumed.
  This is a **universal, upstream-documented gotcha** (the worker-controller README: "if
  `temporal_slot_utilization` returns no data, check the metric names on a running pod"), not a
  local artifact.
- **Recording-rule output name is `temporal_slot_utilization`** (underscore), matching the
  worker-controller reference + its KEDA/HPA examples — so those examples apply to us verbatim. (An
  earlier colon-convention name `temporal:slot_utilization` was our artifact; removed in PR #16.)
- **Rule groups by version-stable, SDK-native labels** (`namespace`, `worker_type`, `task_queue`),
  verified on the raw `:9000` endpoint. It deliberately does **not** use `temporal_io_deployment_name`
  — that is a `kubernetes-pods` scrape-relabel artifact (the pod's `temporal.io/deployment-name`),
  not SDK output. **KEDA selects per WorkerDeployment by filtering `task_queue`** (1:1 to a deployment).
- **Our `temporalio 1.28.0` emits the OLDER label scheme:** bare `namespace`, no
  `temporal_worker_deployment_name` / `temporal_namespace` / `temporal_worker_build_id`. Upstream's
  reference rule groups by those newer labels. **Full label convergence with upstream needs a Core SDK
  bump** — its own decision/phase, not required for autoscaling to work.

## Next

1. **Worker-health signals/alerts** (deferred from the metrics phase): schedule-to-start p99,
   sync-match rate, slots-available, sticky-eviction, backlog. The scrape + store exist; this is
   authoring dashboards/alert rules over them. Units are **seconds** (`durations_as_seconds=True`).
2. **Install KEDA** as the sole external-metrics provider (not prometheus-adapter; they collide on
   the `external.metrics.k8s.io` APIService). Worker-controller bump (0.27.0) deploy-validated.
3. **Autoscaling phase:** wire KEDA Prometheus scaler (`minReplicaCount: 1`) on the steady orders
   queue, querying `temporal_slot_utilization{task_queue="…"}`; build the bursty demo workload + KEDA
   Temporal scaler (scale-to-zero, composite guard) on its queue; validate scaling actions.
4. **Promote ADR-0023 to Accepted** once both behaviors are demonstrated; wire a line into
   `docs/ARCHITECTURE.md`.
