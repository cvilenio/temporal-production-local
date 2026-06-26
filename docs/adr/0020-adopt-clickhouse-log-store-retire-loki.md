# ADR-0020: Adopt ClickHouse as the log store; retire Loki and remove ClickStack/HyperDX

- **Status:** Accepted
- **Date:** 2026-06-26
- **Related:** Supersedes the backend choice in ADR-0019 §3 (the three-way A/B). Builds on
  ADR-0018 (structured logging) and ADR-0019 §1–§2 (message-as-body convention; the node agent
  builds the OTel record), both of which stand unchanged.

## Context

ADR-0019 settled the log emit/collection convention and framed the *log store* under Grafana as a
three-way choice: **A** Loki+Grafana (committed default), **B** ClickStack/HyperDX (opt-in
explorer), **C** ClickHouse store + Grafana viz (the Observability-2.0 / wide-events direction).
It deferred the verdict, wiring an in-Grafana A/B (Loki vs a ClickHouse datasource) to gather
evidence. Crucially, the "ClickHouse" side of that A/B was a **shortcut**: it read HyperDX's
*bundled* ClickHouse through HyperDX's embedded collector, which carried local-app-mode hacks, a
fixed ingestion key, and a non-standard `otel_logs` schema.

The A/B closed in favor of ClickHouse: schema-on-read / full fidelity beat Loki's upfront
field-mapping for the unknown-unknowns (MTTR) case, and the SQL-vs-LogQL cost was judged
acceptable for a single-pane-of-glass that's already Grafana. This ADR hardens that choice into a
committed pipeline and removes the bake-off scaffolding.

## Decision

1. **ClickHouse is the committed log store.** Standalone `clickhouse-server`, read by Grafana's
   `grafana-clickhouse-datasource` (uid `clickhouse-logs`) over its HTTP port.
2. **A standalone OTel Collector is the ingest gateway.** Grafana Alloy has no native ClickHouse
   exporter ([grafana/alloy#3492](https://github.com/grafana/alloy/issues/3492)) — it only ships
   `otelcol.exporter.otlp`/`otlphttp`. So the standard shape applies:
   `Alloy (filelog → OTLP) → OTel Collector (contrib clickhouseexporter) → ClickHouse`. The
   collector owns the **standard** `otel_logs` schema (`create_schema: true`), which is the "real
   Option C" ADR-0019's gotchas pointed at — no HyperDX, no bundled-schema workarounds beyond the
   datasource pins the standard schema still requires (`Timestamp` not `TimestampTime`; `Map`
   attribute columns).
3. **Loki is retired (functionally).** Alloy's `loki.write` branch and the opt-in `clickstack.enabled`
   gate are removed; the single committed path is the OTel one. `grafana/otel-lgtm`'s bundled Loki
   process stays (it can't be cleanly removed from the all-in-one) but receives nothing and is
   unused; lgtm is kept for Grafana + Prometheus + Tempo (metrics/traces).
4. **ClickStack/HyperDX is removed.** The `compose/clickstack.yml` overlay (HyperDX all-in-one) is
   deleted. The losing explorer UI is gone; only the ClickHouse store it once bundled remains, now
   standalone.

## Topology

```
kind pods (obslog JSON stdout)
  → Alloy DaemonSet (otelcol.receiver.filelog → batch → otelcol.exporter.otlphttp)
  → host.docker.internal:4320  (OTLP/HTTP)
  → OTel Collector  [compose] (otlp receiver → clickhouseexporter)
  → ClickHouse      [compose] default.otel_logs
  → Grafana (lgtm)  ClickHouse datasource → orders-logs.json
```

Implementation: `docker-compose.yml` (clickhouse + otel-collector services, lgtm plugin +
datasource mount), `compose/observability/otel-collector/config.yaml`, the `alloy` chart `0.4.0`
(`deploy/charts/alloy`), the cluster Terraform layer (`alloy_clickhouse_otlp_url`), the
`ClickHouse (logs)` datasource, and `orders-logs.json` (SQL, not LogQL).

## Consequences

- **Single pane preserved, mapping removed.** Grafana stays the one viz surface for logs, metrics,
  and traces; logs are schema-on-read with no agent-side field list to maintain (the Loki
  structured-metadata ceiling is gone). Ad-hoc per-call fields are now individually queryable.
- **Query language is SQL.** Dashboards and Explore use ClickHouse SQL with the plugin's macros
  (`$__timeFilter`, `$__timeInterval`) and bracket Map access (`LogAttributes['order_id']`), not
  LogQL. This is the accepted learning cost for ex-Grafana muscle memory.
- **New stateful dependency.** ClickHouse + a collector replace the object-store-cheap Loki on the
  local workbench (more memory). Acceptable here; for a real deployment this is the
  operate-ClickHouse cost ADR-0019 named.
- **Trace correlation field-ready.** `trace_id` still populates the OTel TraceId; logs↔traces
  links light up once traces ship to ClickHouse (or stay on Tempo for now). The datasource already
  carries a `traces`/`otel_traces` block for that day.
- **Datasource pins remain (by choice, not necessity).** The standard `clickhouseexporter` schema
  carries `Map` attribute columns, so the datasource uses `selectContextColumns` to render
  `order_id`/`trace_id`/etc. as expandable fields, and Map filters use bracket access
  (`LogAttributes['order_id']`) in SQL. The columns are pinned explicitly (`Timestamp`,
  `SeverityText`, `Body`) rather than via an `otelVersion` preset — version-independent and
  banner-free. (Unlike HyperDX's bundled store, this exporter's schema *does* carry both
  `Timestamp` and `TimestampTime`, so the preset would also work; the pins are the conservative
  choice.) The win over the HyperDX shortcut is removing HyperDX and owning the schema.
- **Single-pane end state (future):** folding metrics + traces into ClickHouse and retiring lgtm
  remains the long-horizon option ADR-0019 sketched; out of scope here — Grafana over lgtm
  (Prometheus/Tempo) + ClickHouse (logs) is the current pane.
