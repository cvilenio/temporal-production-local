# ADR-0024: Route OTLP push (business) metrics to ClickHouse; keep Prometheus for pull/operational metrics

- **Status:** Accepted
- **Date:** 2026-06-29
- **Related:** Mirrors ADR-0020 (ClickHouse log store) for the metrics signal. Builds on
  ADR-0018 (structured logging) and ADR-0021 (Prometheus pull pipeline + durable
  `prometheus-store`). Does not touch ADR-0023 (KEDA autoscaling) — its scale signal stays
  on the pull pipeline.

## Context

ADR-0021 shipped the **pull** metrics pipeline: workers expose Temporal SDK operational
metrics on `:9000`, the in-cluster Prometheus scrapes them plus the Temporal Cloud
OpenMetrics endpoint, evaluates the `temporal_slot_utilization` recording rule (KEDA's
signal), and `remote_write`s to a durable host `prometheus-store` (15d) that Grafana reads.

A second metrics class exists and is already instrumented: **business / custom metrics**,
emitted via `business_meter()` (`libs/orders/.../shared/metrics.py`) in activities and the
API — `orders.payments_captured` (counter) and `orders.payment_amount` (histogram) in
`activities/external.py`. These ride the OTLP **push** transport.

Today that push terminates at lgtm's bundled OTel Collector → lgtm's bundled Prometheus.
That is the wrong store, for the same reason Loki was the wrong log store (ADR-0019/0020):

- **Cardinality.** Business metrics carry high-cardinality dimensions (sku, customer,
  currency, order attributes). Prometheus indexes every label set — high cardinality is a
  cost/stability hazard. ClickHouse is columnar and built for it.
- **Fidelity & retention.** Prometheus is aggregate-first and short-retention by design
  (our store: 15d). Business metrics are analytical: we want complete persistence and the
  ability to slice/join after the fact — a warehouse workload.
- **Query power.** SQL with joins (e.g. correlate a revenue dip with its error logs on
  `trace_id` in the *same* store) beats PromQL for analytical questions.
- **Durability gap.** The push metrics never reached `prometheus-store` at all — they lived
  only in lgtm's ephemeral bundled Prometheus.

The narrow set that *needs* Prometheus is exactly the set that speaks PromQL natively:
**KEDA, HPA, and Prometheus recording/alert rules.** No business metric requires that today.

## Decision

1. **Split by metric purpose, not by transport.**
   - **Operational / autoscaling** (low-card, aggregate, alerting, KEDA): **Prometheus.**
     Emitted via `workflow.metric_meter()` / `activity.metric_meter()` (replay-safe, pull).
     Unchanged from ADR-0021.
   - **Business / analytical** (high-card, high-fidelity, dashboards, retention):
     **ClickHouse**, fed by the existing standalone OTel Collector. Emitted via
     `business_meter()` (OTLP push).

2. **The standalone OTel Collector is the metrics ingest gateway**, exactly as it is for
   logs (ADR-0020). Add a `metrics` pipeline to `compose/observability/otel-collector/config.yaml`
   using the same contrib `clickhouseexporter` (`create_schema: true` owns the standard
   `otel_metrics_*` tables). One collector, one ClickHouse, two signals (logs + metrics).

3. **Separate the push-metrics endpoint from the trace endpoint in the SDK.** `appkit`
   gains an optional `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` (settings field
   `otel_exporter_otlp_metrics_endpoint`, falling back to the trace endpoint when unset).
   - Traces continue to push to lgtm (Tempo) on `:4317`.
   - Business metrics push to the standalone collector on `:4319` (host) /
     `otel-collector:4317` (in-network).

4. **Delta temporality for the push pipeline.** The OTLP metric exporter uses DELTA
   aggregation temporality (Counter/Histogram/ObservableCounter delta; UpDownCounter/Gauge
   cumulative). Delta is the natural warehouse shape: each export is the increment in its
   window, so a SQL `sum(Value)` over a time range is an exact count — no Prometheus-style
   `rate()` over cumulative series required.

5. **Metric names are stored un-mangled.** The Prometheus exporter rewrote
   `orders.payments_captured` → `orders_payments_captured_total` (dots→underscores,
   `_total` suffix). The ClickHouse exporter stores the **raw OTel name**
   (`orders.payments_captured`, `orders.payment_amount`). Dashboards query the raw names.

## Topology

```
BUSINESS / PUSH (high-fidelity)                         OPERATIONAL / PULL (autoscale+alert)
  business_meter()  (activities, API)                    *.metric_meter()  (workflow, activity)
   → OTLP gRPC                                             → SDK /metrics :9000  (scrape)
   → host.docker.internal:4319  (otel-collector)          → in-cluster Prometheus
   → contrib clickhouseexporter                            → recording rule (slot_utilization → KEDA)
   → ClickHouse  default.otel_metrics_{sum,histogram,…}    → remote_write → prometheus-store (15d)
   → Grafana (ClickHouse datasource, SQL)                  → Grafana (Prometheus datasource, PromQL)

TRACES → still lgtm:4317 → Tempo   (unchanged)
LOGS   → Alloy → otel-collector → ClickHouse otel_logs   (ADR-0020, unchanged)
```

## Consequences

- **Business metrics gain warehouse properties**: full retention, high cardinality, SQL,
  and co-location with logs in ClickHouse (join on `trace_id` / resource attrs).
- **Mixed-datasource business dashboard.** `business.json` panels split by store: the two
  push panels (payments captured, payment amount) move to ClickHouse SQL; the three pull
  panels (workflow steps, compensations, capture-duration) stay on Prometheus. This is the
  architecture made visible, not an accident.
- **Two query languages** for business observability (SQL + PromQL), both fronted by
  Grafana — the same cost already accepted for logs in ADR-0020.
- **Works on both substrates.** On kind, apps push to the host collector via
  `host.docker.internal:4319`; on compose, via `otel-collector:4317`. ClickHouse is the
  business-metrics store in every run mode.
- **Dual-purpose escape hatch.** If a business metric ever needs to drive a Prometheus
  alert/KEDA, fan it out in the same collector pipeline with a second
  `prometheusremotewrite` exporter → `prometheus-store`. Not the default.
- **lgtm still required** (ADR-0021): Grafana, Tempo (traces), and the bundled Prometheus
  (Local-OSS compose store). This ADR removes one more reason it is load-bearing (business
  metrics no longer depend on its bundled Prometheus), nudging toward the eventual
  single-warehouse end state (metrics + logs + traces in ClickHouse, lgtm → grafana-only).
  That consolidation remains out of scope — its own ADR.

## Implementation

`docs/adr/0024-*` (this file); `compose/observability/otel-collector/config.yaml` (metrics
pipeline); `libs/appkit/python/appkit/{settings.py,telemetry.py}` (metrics endpoint split +
delta temporality); the three composition roots (`orders-api`, workflow worker, activity
worker `dependencies.py`); `deploy/charts/orders-api` + `deploy/charts/orders-workers`
(values + env + Chart.yaml version bump); `deploy/terraform/layers/cluster/variables.tf`
(the chart-version pins — bumping `Chart.yaml` alone does NOT redeploy; the ArgoCD
`targetRevision` is set from these terraform vars); `compose/host-apptier.yml` (compose-mode
env); `compose/observability/grafana/dashboards/business.json` (push panels → ClickHouse SQL,
pull panels → `prometheus-kind`).

Emission already existed and was already correctly split — `business_meter()` (push) in
`libs/orders/.../activities/external.py`; `workflow.metric_meter()` (pull) in the workflow.
This ADR only re-routed the push transport and migrated the consuming panels.

## Operational notes (verified live, 2026-06-29)

A real order (`capture_payment`) confirmed end-to-end: `orders.payments_captured=1`,
`orders.payment_amount` Sum=order amount, `AggregationTemporality=DELTA`,
`service.name=orders-worker-activity` in ClickHouse. Two gotchas surfaced and are worth
keeping:

- **ClickHouse stores raw OTel names.** `orders.payments_captured` / `orders.payment_amount`
  — NOT the Prometheus-mangled `orders_payments_captured_total`. SQL panels query the raw name.
- **Grafana datasource rename is a boot-breaker.** Provisioning matches datasources by *name*;
  renaming one while keeping its `uid` makes Grafana try to INSERT the new name on an
  in-use uid → `data source with the same uid already exists`, which **aborts Grafana
  startup** on any volume that still holds the old row. Always pair a rename with a
  `deleteDatasources:` entry for the old name (see `datasources/clickhouse.yaml`).
- **Air-gap: disable Grafana plugin preinstall.** `GF_PLUGINS_PREINSTALL_DISABLED=true` on
  lgtm — otherwise a restart blocks the HTTP listener trying to reach grafana.com
  (IPv6-only, no route here).
- **CORRECTION (2026-07-01, checkpoint 0026): `GF_INSTALL_PLUGINS` never worked on this
  image, and nothing was ever cached.** The line above originally claimed the ClickHouse
  plugin was installed via `GF_INSTALL_PLUGINS` and survived restarts from the `lgtm-data`
  volume. Both halves were wrong: `grafana/otel-lgtm`'s `run-grafana.sh` execs
  `bin/grafana server` directly — it never runs the `grafana-cli plugins install` step the
  official `grafana/grafana` image's entrypoint uses for that env var, so it was a silent
  no-op from the start; the ClickHouse datasource had been failing with
  `plugin.notRegistered` the whole time. Fixed by pre-fetching the plugin the same way as
  the Headlamp UI plugins (sha256-verified, `config/dependencies.yaml` `grafana.plugins`,
  `compose/scripts/fetch-grafana-plugins.py`, `just grafana-plugins`) into a bind-mounted
  `compose/deployment/grafana/plugins/` → `GF_PATHS_PLUGINS`. Two more gotchas surfaced
  fetching it as a **zip** (unlike the Headlamp plugins, which are tarballs):
  Grafana's plugin-signature check hashes every file under the plugin dir against its
  signed `MANIFEST.txt`, so the fetch script's own version-stamp file must live *outside*
  the plugin dir (a sibling `.{name}.version`), not inside it — one stray file makes the
  whole plugin look "modified" and unloadable. And `zipfile.extractall` (unlike `tarfile`)
  does not restore Unix permissions, so the plugin's backend binaries (`gpx_clickhouse_*`)
  lost their executable bit and failed with `permission denied` on load — the fetch script
  now chmods each entry from its zip `external_attr` after extracting.
- **CORRECTION (2026-07-01, checkpoint 0026): the substrate-specific datasource note above
  described the *correct* target, but the dashboards didn't follow it.** All five
  `dashboards-critical/*.json` panels were hardcoded to `uid=prometheus` (lgtm's bundled,
  compose-only instance) instead of `uid=prometheus-kind` — on kind+Cloud this meant every
  panel silently rendered "No Data," not because pull metrics were unwired but because the
  dashboards pointed at the wrong store. Fixed by repointing all five to `prometheus-kind`
  and, further, making the Critical Flows dashboards backend-agnostic: RPS/error/latency/
  availability panels now carry two query targets each — one against the OSS metric
  (`service_requests` etc., `rate()`'d) and one against its `temporal_cloud_v1_*`
  equivalent (already a precomputed rate/percentile) — so whichever backend is live
  populates the panel with no manual switching. What can't be unified this way (persistence,
  internal task processing, shard, process metrics — below the customer-facing API boundary
  Temporal Cloud never exposes) moved to a new **Temporal Self-Hosted Internals** Grafana
  folder instead of pretending it would populate on Cloud. See checkpoint 0026.
</content>
</invoke>
