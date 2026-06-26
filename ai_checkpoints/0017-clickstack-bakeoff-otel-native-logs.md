# 0017 — ClickStack-vs-Loki log bake-off → OTel-native log convention

- **Status:** **LANDED + LIVE-VALIDATED ON KIND+CLOUD (2026-06-26).** Alloy chart `0.3.3`,
  image `v1.17.0`. obslog emits `message`; both Loki and ClickStack render it as the log line.
  Decision captured in **ADR-0019**.
- **Date:** 2026-06-26
- **ADRs:** **ADR-0019** (new). Builds on ADR-0018 (structured logging).

## Why

ADR-0018's follow-up: Grafana Explore's structured-log UX was weak, and the logs shipped as a
flat JSON blob with the message under structlog's `event` key (no message-vs-context split). Ran
a real ClickStack (ClickHouse + OTel collector + HyperDX) bake-off vs Loki/Grafana, then fixed
the root cause (emit convention) and settled the backend question on org-fit grounds.

## Done this session (code + docs)

- **ClickStack overlay** (`compose/clickstack.yml`, new): HyperDX all-in-one, opt-in, additive,
  ports chosen off the base map (UI 8080, OTLP 4319/4320, CH 8123/9000). Fully declarative:
  **local-app-mode** (entrypoint override strips the image's hardcoded `REQUIRED_AUTH` so the
  `IS_LOCAL_APP_MODE` env wins) → **no login**, static team, sources seeded at boot from
  `DEFAULT_SOURCES`/`DEFAULT_CONNECTIONS` env, **fixed** `INGESTION_API_KEY`. `down -v` + `up`
  returns fully configured with zero manual steps. (An earlier `bootstrap.sh` was needed before
  local-app-mode; deleted.)
- **obslog `event` → `message`** (`libs/logging/python`): `EventRenamer(schema.MESSAGE)`;
  `schema.py` `MESSAGE="message"`; `log-schema.json` + conformance test updated. `poe lint`/`test`
  green. The "opt into the convention at the source" change.
- **Alloy → OTel-native collection** (`deploy/charts/alloy`, `0.1.0`→`0.3.3`, image `v1.5.1`→
  `v1.17.0`):
  - **Loki branch:** parse JSON → `level` label → contract fields + `exception` as **structured
    metadata** → stored line = `message` (raw-line fallback). Clean Drilldown/Explore, native
    facets, `trace_id` structured-metadata lights up lgtm's pre-provisioned Loki→Tempo derived
    field (log↔trace ready for tracing).
  - **ClickStack branch (gated `clickstack.enabled`):** `otelcol.receiver.filelog`
    (`include_file_path=true`, `start_at=end`, public-preview → `--stability.level` arg) with
    `container`/`json_parser`/`move message→Body`/`severity_parser`/`trace_parser`. Real OTel
    record: Body=message, attributes, severity, TraceId, k8s resource meta from path.
  - TF wiring: `applications.tf` helm `valuesObject` + `variables.tf` `alloy_chart_version` +
    `alloy_clickstack_*` (enabled default false; fixed ingestion key default).
- **Grafana Logs dashboard** (`compose/observability/grafana/dashboards/orders-logs.json`, new):
  message-as-line logs panel + log-volume-by-level + `$container` filter (provisioned/declarative).
- **Option C — Grafana-over-ClickHouse** (clickstack overlay extends lgtm): installs
  `grafana-clickhouse-datasource` (`GF_INSTALL_PLUGINS`) + provisions a **ClickHouse (logs)**
  datasource (`compose/observability/grafana/provisioning/datasources/clickhouse.yaml`) →
  `clickstack:8123` `api/api`, OTel schema on `default.otel_logs`. Grafana now reads logs from
  ClickHouse (schema-on-read, zero field-mapping) **alongside** Loki — A/B in Explore, both
  inside Grafana, no Loki-vs-second-tool. Reframes the decision to **three-way** (Loki / HyperDX /
  Grafana-over-ClickHouse); ADR-0019 updated + grounded in the Observability-2.0 / wide-events
  literature. Decision: Grafana is the pane; Loki default today; ClickHouse the strategic path.

## Verification

- **obslog:** `poe lint` (ruff+pyright) + `poe test` 3/3 green; worker pod stdout emits
  `"message":` (not `event`).
- **Loki (live):** worker line reads `terminal finalization` (clean message, not JSON);
  `{… } | order_id!=""` / `| trace_id!=""` filter on structured metadata (no `| json`) →
  90–104 lines/window. Volume-by-level uses the `level` label.
- **ClickStack (live):** `down -v` + `up` → no login (`/api/me` returns Local App Team), 4
  sources auto-seeded, fixed key accepted, `Body`=message, `SeverityText` set, `TraceId`
  populated where `trace_id` present, k8s resource meta from path; filelog container errors
  0/30s after `include_file_path`.
- **Decision:** stay on Grafana for logs; ClickStack parked opt-in (ADR-0019).

## Gotchas / observations

- **Convention lives at source+agent, not per-backend.** Dead-ends: hand-listing fields into
  labels (brittle, loses unlisted fields); per-backend display expressions (HyperDX
  `bodyExpression`). Both are workarounds; the fix is `message`-as-body at emit + the agent
  building the record (Cloud-Logging model).
- **Helm eats `{{ }}`.** Alloy template braces must be wrapped in a Helm raw-string
  (`{{ `…` }}`); literal `{{ }}` even in a *comment* breaks `helm template`.
- **`otelcol.receiver.loki` bridge can't carry arbitrary attributes** (structured metadata
  didn't survive it) and sets Body=line — which is why the ClickStack path moved to the
  `filelog` receiver, and Loki uses native structured metadata on its own `loki.write` path.
- **HyperDX create-path resets complex display expressions to `Body`** (only PUT/update keeps
  them) — irrelevant now that Body is natively the message.
- **grafana-clickhouse-datasource against HyperDX's bundled CH (Option C) — schema-shape snags,
  not Option-C flaws.** Reusing HyperDX's `otel_logs` (non-standard OTel-exporter schema): (1) the
  plugin's OTel `latest` preset hardcodes the filter column to `TimestampTime`, absent here (only
  `Timestamp`) → DON'T set `otelVersion`; pin `timeColumn`/`filterTimeColumn=Timestamp`,
  `levelColumn=SeverityText`, `messageColumn=Body`. (2) Context fields don't render unless you set
  `selectContextColumns=true` + `contextColumns=[LogAttributes,ResourceAttributes]`. (3) Builder
  *filters* emit dot-access (`LogAttributes.order_id`) but the column is `Map(LowCardinality(String),
  String)` → use the SQL editor with bracket access `LogAttributes['order_id']`. A real Option C
  (standalone ClickHouse + OTel Collector `clickhouseexporter`, standard schema) avoids all three.
- **filelog `start_at=end`** avoids the restart re-flood the old loki.source emptyDir-positions
  path caused (saw 187k "sending queue is full"/30s before scoping + this).
- Loki rename `event`→`message`: only checkpoint-0016 notes referenced `.event`; no live
  dashboard/query broke.

## Next / follow-ups

- **Metrics pillar (next ADR):** Prometheus pull on kind + Temporal Cloud OpenMetrics
  (`metrics.temporal.io`), into Grafana — the pane is settled.
- **Tracing:** when enabled, traces → Tempo; the Loki→Tempo `trace_id` derived field already
  fires. (HyperDX log→trace additionally needs traces in ClickStack.)
- **Debugging muscle:** lean on Logs Drilldown + Explore for unknown-breakage triage; graduate
  discovered failure modes into dashboard panels + alerts (MTTD ← MTTR loop).
