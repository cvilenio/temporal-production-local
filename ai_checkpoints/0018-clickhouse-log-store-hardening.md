# 0018 — Harden the log pipeline: ClickHouse-only, retire Loki + ClickStack/HyperDX

- **Status:** **LANDED + LIVE-VALIDATED ON KIND (2026-06-26).** Alloy chart `0.4.0`. Real kind pod
  logs flow filelog → OTLP → standalone OTel Collector → standalone ClickHouse → Grafana. Loki
  retired; HyperDX removed. Decision captured in **ADR-0020** (supersedes ADR-0019 §3).
- **Date:** 2026-06-26
- **ADRs:** **ADR-0020** (new). Builds on ADR-0019 (§1 message-as-body + §2 agent-builds-the-record
  stand; §3 backend choice superseded) and ADR-0018 (structured logging).

## Why

Checkpoint 0017 closed the three-way bake-off with ClickHouse as the strategic store but left it
wired as an *opt-in A/B*: Loki was still default, and "ClickHouse" was HyperDX's **bundled** store
reached through HyperDX's embedded collector (local-app-mode hacks, fixed ingestion key,
non-standard schema). This session hardens the verdict into a single committed pipeline on a
**standalone** ClickHouse + OTel Collector, turns Loki off, and scrubs the bake-off scaffolding.

**Load-bearing constraint:** Grafana Alloy has no native ClickHouse exporter (grafana/alloy#3492) —
only `otlp`/`otlphttp`. So the standard shape is `Alloy → OTLP → OTel Collector
(contrib clickhouseexporter) → ClickHouse`. This is the "real Option C" ADR-0019's gotchas named;
the collector owns the standard `otel_logs` schema.

## Done this session (code + docs)

- **Base compose** (`docker-compose.yml`): added `clickhouse` (clickhouse/clickhouse-server:24.8,
  8123/9000, `clickhouse-data` vol) and `otel-collector`
  (otel/opentelemetry-collector-contrib:0.116.1, host 4319/4320 — the old ClickStack ports, so
  Alloy's endpoint is unchanged). Extended `lgtm` with `GF_INSTALL_PLUGINS=grafana-clickhouse-datasource`
  + the datasource mount (moved out of the deleted overlay). Dropped the Loki `3100` host publish.
  New collector config `compose/observability/otel-collector/config.yaml` (otlp → batch →
  clickhouseexporter, `create_schema:true`). **Deleted `compose/clickstack.yml`** (HyperDX
  all-in-one + local-app-mode hacks gone).
- **Alloy chart `0.3.3 → 0.4.0`** (`deploy/charts/alloy`): removed the entire Loki branch
  (discovery/relabel/file_match/loki.process/loki.write), removed the `clickstack.enabled` gate
  (single always-on path), dropped the OTLP `authorization` header (local collector is open).
  values `clickstack.* → clickhouse.{scopeNamespace,otlpUrl}`; daemonset `--stability.level=public-preview`
  now unconditional, env `CLICKHOUSE_OTLP_URL`.
- **Terraform cluster layer**: `alloy_clickstack_* → alloy_clickhouse_otlp_url` (dropped
  `enabled`/`ingestion_key`), `alloy_chart_version=0.4.0`, valuesObject `clickhouse{}`.
- **Grafana datasource** (`…/datasources/clickhouse.yaml`): `host: clickhouse`, committed creds,
  kept the column pins + `selectContextColumns`. **Dashboard** (`orders-logs.json`): both panels +
  `$container` var converted from LogQL to ClickHouse SQL (uid `clickhouse-logs`).
- **Docs**: ADR-0019 status → superseded-in-part by ADR-0020 (kept the A/B/C analysis as the
  reasoning); new **ADR-0020** records the verdict + hardened topology. Config comments scrubbed of
  bake-off framing; technical rationale kept.
- **Platform console** (`apps/platform/console/.../services/status/core.py` + `templates/architecture.html`):
  added `clickhouse` (http_probe `/ping`) and `otel-collector` (TCP probe :4318) to the
  `SERVICE_REGISTRY` so the new log infra shows live status on the `/architecture` page. While
  there, split the now-large "Tooling & Infrastructure" strip into labeled sub-sections via a new
  per-entry `subgroup` field (flows to the frontend through the snapshot spread; `toolingKeys()` →
  `toolingSections()`): **Cluster & Delivery** (kind, registry, ArgoCD, Headlamp, viz-proxy),
  **Observability** (lgtm, clickhouse, otel-collector), **Consoles & Utilities** (console, pgweb,
  ui-proxy, codec). Fixed section order, empty sections skipped, unknown subgroups appended.

## Gotchas / observations

- **ClickHouse 24.x generates a RANDOM default-user password on first boot** when none is given →
  collector + Grafana auth fail with `code: 516 AUTHENTICATION_FAILED`. The local
  `clickhouse-client` healthcheck still passes (local conns trusted), so the container reads
  "Healthy" while the network path is broken — a masked failure. Fix: committed
  `CLICKHOUSE_PASSWORD` (+ `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1`), same value in collector +
  datasource. Password change needs the data volume wiped (user setup only runs on empty dir).
  (Host `curl -u user:pass` also failed oddly; `?user=&password=` query params or
  `X-ClickHouse-User/Key` headers work.)
- **Corrects 0017:** the contrib clickhouseexporter 0.116 `otel_logs` schema HAS both `Timestamp
  DateTime64(9)` AND `TimestampTime DateTime` (the `main` README lags). Datasource pins still used
  (version-independent, banner-free), not because TimestampTime is missing. `LogAttributes`/
  `ResourceAttributes` are `Map(...)` → bracket access in SQL (`LogAttributes['order_id']`).
- **Resource-attr keys are dotted OTel semconv** (`k8s.namespace.name`, `k8s.container.name`) — the
  dashboard SQL filters on `ResourceAttributes['k8s.namespace.name']='orders'`.
- **Foreign lines survive:** orders-api uvicorn access logs (non-obslog) land with raw Body + empty
  SeverityText — the filelog `move` `if` guard + `on_error=send_quiet` keep them flowing.
- **start_at=end** means only post-restart lines ship (intended; avoids the re-flood). The old
  0.3.3 DaemonSet had started erroring once the Loki 3100 publish was dropped — rolling 0.4.0
  fixed it.

## Verification

- **Collector→CH→Grafana (synthetic OTLP):** a hand-built OTLP/HTTP push to :4320 landed with
  Body=message, SeverityText, `k8s.*` resource attrs, `LogAttributes['order_id']`, TraceId; the
  three dashboard SQLs + datasource health (`Data source is working`) + queries-through-Grafana
  (`/api/ds/query`, logs + timeseries) all green. Synthetic rows truncated after.
- **Live kind (real pod logs):** `just platform-up` rolled Alloy `0.4.0` (image v1.17.0, 3/3 ready,
  deployed config has **0** `loki.write`). ClickHouse then filled with real `worker` (clean
  `order workflow completed` / `step completed` messages), `orders-api` (raw uvicorn lines), and
  `postgres` rows. Grafana logs query over real data returned 42 rows, no error; the
  `orders-logs.json` dashboard provisioned with both panels on the ClickHouse datasource (0 Loki
  refs). No workflow execution was initiated for this validation (existing demo traffic only).

## Next / follow-ups

- **Metrics pillar:** unchanged — lgtm Prometheus (kind + Temporal Cloud OpenMetrics) + Grafana.
- **Tracing:** when enabled, traces → ClickHouse `otel_traces` lights up logs↔traces in the same
  datasource (the `traces` block is pre-wired); until then traces stay on Tempo.
- **Single-pane end state:** folding metrics+traces into ClickHouse and retiring lgtm remains the
  long-horizon option (ADR-0019/0020) — out of scope now.
