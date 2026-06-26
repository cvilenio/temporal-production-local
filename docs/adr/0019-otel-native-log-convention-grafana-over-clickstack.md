# ADR-0019: OTel-native log convention at the source; Grafana viz, log store a three-way choice (Loki default, ClickHouse path)

- **Status:** Accepted
- **Date:** 2026-06-26
- **Related:** ADR-0018 (structured logging — `obslog`, the shared schema, Alloy node-agent
  collection; this ADR evolves its emit/collection convention and settles the backend question
  the 0018 follow-up spike opened).

## Context

ADR-0018 landed structured logging but left two things open: (1) the emitted record put the
human message under structlog's `event` key as a peer of all other fields — no
message-vs-context distinction, so backends showed the whole JSON blob as the log line; and
(2) a follow-up spike was to evaluate **ClickStack** (ClickHouse + OTel collector + HyperDX)
against the committed **Loki + Grafana** path for log-exploration UX, since Grafana Explore's
structured-log ergonomics were weak.

The spike ran dual-ship (both backends fed the same `orders` logs) and surfaced the real
tradeoffs — not just UX taste, but the underlying data model.

## Decision

### 1. Adopt the OTel-native message-as-body convention at the source

`obslog` emits the human message under **`message`** (was `event`), via
`structlog.processors.EventRenamer`. This is the cross-tool convention (OTel maps it to the
LogRecord Body; Cloud Logging / Datadog / OTel agents recognize `message` as the display line).
The schema contract (`libs/logging/schema/log-schema.json`) and conformance test moved with it.
**"Opt into the convention at the source"** — the app names the message field conventionally;
the agent maps it to Body. No per-backend display configuration.

### 2. The node agent builds the OTel record (both backends)

The stdout-tail pattern from ADR-0018 stays (faithful k8s collection — apps hold no backend
endpoint). The split of message-vs-attributes is reconstructed at the **Grafana Alloy** agent,
exactly as a Cloud Logging / Datadog agent does:

- **Loki path:** parse the JSON → `level` stays a label → all other contract fields ride as
  **structured metadata** (Loki 3, high-cardinality-safe) → set the stored line to `message`
  (raw-line fallback for foreign/non-JSON). Result: clean Drilldown/Explore preview, native
  facets, and `trace_id` as structured metadata lights up lgtm's pre-provisioned Loki→Tempo
  derived field (one-click log→trace once tracing lands).
- **ClickStack path (opt-in):** an `otelcol.receiver.filelog` builds a real OTel LogRecord
  (`container` → `json_parser` → `move message→Body` → `severity_parser` → `trace_parser`),
  so Body=message, attributes auto-flatten, severity + TraceId populate — no per-backend
  display config either.

Rejected along the way: hand-listing fields into labels (brittle, loses unlisted fields), and
per-backend display expressions (HyperDX `bodyExpression`) — both treat a workaround as the
fix. The convention belongs at source+agent.

### 3. Grafana is the viz pane. The log *store* is a three-way choice, not Loki-vs-HyperDX.

**Grafana wins on fit, not features** for this org/role (Platform Architect, production
reliability of Temporal workloads): already internal, ex-Grafana teammates, and the single pane
for metrics (Temporal Cloud OpenMetrics + SDK). So the real question is the **log store under
Grafana**, and there are three options — the framing is not the binary the bake-off started with:

- **Option A — Loki + Grafana** (committed path). Cheap object-store economics, LogQL, Logs
  Drilldown. But Loki indexes low-cardinality labels (structured-metadata default ~15 attrs) and
  is **curate-upfront**: clean message-line + faceted fields requires *naming* the fields at the
  agent (the structured-metadata list in the alloy chart). That list is the physical symptom of
  Loki's ceiling, not a discipline lapse — Loki has no generic JSON auto-flatten.
- **Option B — ClickStack / HyperDX** (separate explorer UI). Explorer-first, generic
  auto-flatten, columnar high-card. Rejected as the *primary* surface: it's a second tool nobody
  else runs, and its scale/cost edge is moot on a local workbench. Kept as an opt-in overlay.
- **Option C — ClickHouse store + Grafana viz (drop Loki).** The `grafana-clickhouse-datasource`
  reads logs straight from ClickHouse with the OTel schema: rendered logs panels, Explore,
  logs↔traces correlation — **keeping Grafana as the single pane** while getting **schema-on-read,
  full-fidelity, zero-field-mapping** logs. This is the **Observability 2.0 / "wide events"**
  model (store raw high-dimensional events, aggregate at query time) — an industry direction, not
  a preference. Cost: **SQL not LogQL** (ex-Grafana muscle memory is LogQL), Loki's cheap
  economics, and running a stateful columnar DB instead of an object-store sidecar.

**Decision:** Grafana is the pane. **Option A is the default today** (cheapest, in place,
team-fluent). **Option C is the strategically-aligned path** if/when log *exploration* (the
unknown-unknowns case) or the per-field-mapping maintenance becomes the pain — it removes the
mapping entirely and matches where the industry is heading, at the cost of SQL + operating
ClickHouse. To make that an evidence-based choice rather than a bet, both are wired for an
**in-Grafana A/B**: the committed **Loki** datasource and a provisioned **ClickHouse (logs)**
datasource (`grafana-clickhouse-datasource` → `default.otel_logs`, OTel schema), compared in
Explore on the same logs. ClickStack/HyperDX (B) stays an opt-in overlay
(`values.clickstack.enabled=false`; local-app-mode, fixed key, sources seeded — zero manual
steps) as the explorer reference and the source of the ClickHouse `otel_logs` table the C
datasource reads.

## Consequences

- **Debugging posture (the "I don't know where it broke yet" case):** start in **Logs
  Drilldown** (auto-facets, no query) or **Explore**, pivot logs by `order_id`/`trace_id`/
  `workflow_id`/`step` (structured metadata), correlate to the metric that flagged it and to
  the trace (Tempo) via `trace_id`. **Dashboards are for known signals (MTTD); exploration is
  for unknowns (MTTR).** Each incident *graduates* a discovered failure mode into a dashboard
  panel + alert — you grow the dashboard from what exploration teaches, not up front.
- **A committed Logs dashboard** (`compose/observability/grafana/dashboards/orders-logs.json`)
  provides the message-as-line view + level-volume + container filter.
- **Option C is wired for the A/B but via a shortcut, with known schema friction.** The
  `grafana-clickhouse-datasource` (provisioned in `…/datasources/clickhouse.yaml`, clickstack
  overlay) reads HyperDX's **bundled** ClickHouse `otel_logs` — which is NOT the standard OTel
  ClickHouse-exporter schema the plugin expects. Two snags resulted, both schema-shape artifacts,
  not Option-C flaws: (1) the plugin's OTel `latest` preset filters on `TimestampTime`, absent
  here (only `Timestamp`) → don't set `otelVersion`, pin `timeColumn`/`filterTimeColumn=Timestamp`;
  (2) builder-generated **filters** use dot-access (`LogAttributes.order_id`) but the column is
  `Map(LowCardinality(String),String)` → needs bracket `LogAttributes['order_id']` (use the SQL
  editor for Map filters). A real Option-C deployment (standalone ClickHouse + OTel Collector
  `clickhouseexporter`, standard schema) avoids both. Context display + time filtering work; the
  Map-filter-via-builder is the one rough edge. **Do not score this friction against Option C** —
  it's the cost of reusing HyperDX's store for a fast in-Grafana comparison.
- **Tradeoff accepted (Loki):** truly ad-hoc per-call fields not in the schema contract aren't
  individually faceted in Loki (they are in ClickStack's auto-flatten); contract fields +
  `exception` are carried. High-cardinality id filtering is structured-metadata (good), not
  columnar (ClickStack's edge) — fine at local volume.
- **Trace correlation is field-ready now, link-ready when tracing lands:** `trace_id` populates
  the OTel TraceId (ClickStack) and Loki structured metadata (Grafana derived field). Clicking
  through to a trace in HyperDX additionally needs traces shipped to ClickStack — out of scope
  while traces go to Tempo.
- **Next (metrics/traces pillars):** the metrics ADR (Prometheus pull on kind + Temporal Cloud
  OpenMetrics) and tracing remain. If Option C (or ClickStack) is adopted, folding traces+metrics
  into ClickHouse and retiring lgtm is the single-pane end state; until then Grafana is the pane.
- **Open decision, evidence to gather:** run the Loki-vs-ClickHouse Explore A/B on real
  incident-shaped questions (unknown-unknowns, high-card `order_id`/`trace_id` pivots) and weigh
  the result against the SQL-vs-LogQL learning cost for ex-Grafana teammates and the op-cost of
  running ClickHouse. Revisit this ADR with the verdict.

## Grounding

The "full fidelity, schema-on-read, no field mapping" instinct is the named industry direction
(Observability 2.0 / wide events), and "ClickHouse store + Grafana viz" is an officially
supported, in-production pattern — not a workaround:

- Using Grafana and ClickHouse for observability — ClickHouse Docs: <https://clickhouse.com/docs/observability/grafana>
- ClickHouse plugin for Grafana — Grafana Labs: <https://grafana.com/grafana/plugins/grafana-clickhouse-datasource/>
- It's Time to Version Observability (Observability 2.0) — Honeycomb: <https://www.honeycomb.io/blog/time-to-version-observability-signs-point-to-yes>
- Scaling Observability beyond 100PB — wide events, replacing OTel — ClickHouse: <https://clickhouse.com/blog/scaling-observability-beyond-100pb-wide-events-replacing-otel>
- Structured metadata (Loki's high-card mechanism + its limits) — Grafana Loki docs: <https://grafana.com/docs/loki/latest/get-started/labels/structured-metadata/>
- Best Loki Alternatives for Logs — SigNoz: <https://signoz.io/blog/loki-alternatives/>
- From Loki to ClickHouse: scaling log analytics — Skycloak: <https://skycloak.io/blog/loki-to-clickhouse-migration/>
- Network observability without Loki (ClickHouse) — Red Hat: <https://www.redhat.com/en/blog/deploying-network-observability-without-loki-an-example-with-clickhouse>
