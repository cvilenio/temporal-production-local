# 0022 — Business (push) metrics → ClickHouse; /architecture page truth-up

- **Status:** **Landed + verified live** on the kind+Cloud stack (real order end-to-end).
- **Date:** 2026-06-29
- **ADRs:** **ADR-0024** (new, Accepted) — route OTLP push/business metrics to ClickHouse,
  keep Prometheus for pull/operational. Follows ADR-0020 (logs→ClickHouse) and ADR-0021
  (Prometheus pull pipeline).

## Done this session

- **Business/push metrics now warehouse in ClickHouse** (ADR-0024). The push transport split
  from traces: `appkit` gains `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` (settings + `telemetry.py`,
  DELTA temporality), threaded through the three composition roots. Traces stay on lgtm/Tempo
  (`:4317`); business metrics go to the standalone OTel Collector (`:4319`) → ClickHouse
  `otel_metrics_*`. Emission already existed and was already correctly split (`business_meter()`
  push in `activities/external.py`; `workflow.metric_meter()` pull) — only transport changed.
- **OTel Collector** gained a `metrics` pipeline → `clickhouseexporter` (`create_schema` owns
  the `otel_metrics_*` tables). Charts (`orders-api` 0.1.3 / `orders-workers` 0.1.8) + the
  terraform chart-version pins (`variables.tf`) + `host-apptier.yml` carry the new endpoint.
- **`business.json`** is now mixed-store: payments panels → ClickHouse SQL (`clickhouse-logs`),
  workflow/operational panels → `prometheus-kind` (prometheus-store) so they light up on kind.
- **`/architecture` page (console)** truthed up: `prometheus-store` node added (durable 15d
  tier — still required), ClickHouse → "Logs + Metrics", collector desc → "logs + metrics".
  Console is a baked image — rebuilt so the registry change is live.
- **Grafana datasource** renamed "ClickHouse (logs + metrics)" (one CH server backs `otel_logs`
  + `otel_metrics_*`). `docs/ARCHITECTURE.md` Observability section rewritten (was stale:
  logs→Loki, business→Prometheus, autoscaler→prometheus-adapter — all superseded).
- **Verified live:** one happy-path order → `orders.payments_captured=1`, `orders.payment_amount`
  Sum=7350¢ ($73.50), DELTA, `service.name=orders-worker-activity` in ClickHouse. 1 Cloud
  execution.

## Decisions (settled — see ADR-0024)

- Split metrics by **purpose, not transport**: operational/autoscale → Prometheus; business/
  analytical → ClickHouse. KEDA's signal stays pull-side, untouched.
- DELTA temporality on the push pipeline (warehouse-natural; `sum(Value)` = exact count).
- Grafana datasource renames MUST ship a `deleteDatasources:` migration (uid-collision aborts
  Grafana boot). lgtm runs with `GF_PLUGINS_PREINSTALL_DISABLED=true` (air-gap, ADR-0013).

## Open questions

- **Business-metric attributes / dashboards.** The counter currently `.add(1)` with no labels;
  richer business dashboards (per-sku, per-customer) need attributes on emission + the
  high-cardinality slices ClickHouse is chosen for. Owner to define the business cut.
- **lgtm decomposition.** With logs + business metrics off lgtm's bundled Prometheus, the only
  remaining lgtm dependencies are Grafana + Tempo (traces). Folding traces into ClickHouse →
  grafana-only image is now a smaller step — its own ADR when wanted.

## Next

1. Worker-health dashboards/alerts over the pull pipeline (still deferred from the metrics phase
   — see checkpoint 0021): schedule-to-start p99, sync-match, slots-available, backlog.
2. Autoscaling phase (ADR-0023): install KEDA, wire the Prometheus + Temporal scalers.
3. Add attributes to business metrics + build the high-cardinality business views in ClickHouse.
</content>
