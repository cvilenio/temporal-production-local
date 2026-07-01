# 0026 — Grafana dashboards: fix blank panels, build worker/KEDA view, unify backends

- **Status:** Landed + verified live (kind + Cloud path). All queries checked against the
  running Prometheus store and Grafana's own datasource proxy; no live workflow executions
  needed (read-only metrics work throughout).
- **Date:** 2026-07-01
- **ADRs:** Corrects two stale operational notes in ADR-0024 (push metrics to ClickHouse).
  Builds on ADR-0020 (ClickHouse log store), ADR-0023 (KEDA autoscaling), checkpoint 0021
  (metrics + worker autoscaling pipeline), checkpoint 0025 (KEDA phase 1).

## Why

Since checkpoint 0025 landed KEDA autoscaling, the Grafana dashboard page was mostly
blank/"No Data," and there was no single view giving the operator playbook (schedule-to-start
canary → fleet-health quartet → did KEDA react). Separately, the ClickHouse-backed logs/
business-metrics panels were failing with `clickhouse-logs was not found`.

## Done this session

### 1. Fixed the ClickHouse datasource plugin (was never actually loading)

`GF_INSTALL_PLUGINS=grafana-clickhouse-datasource` on the `lgtm` (grafana/otel-lgtm) service
was a silent no-op — that image execs `bin/grafana server` directly and never runs the
`grafana-cli` install step the official `grafana/grafana` entrypoint uses for that env var.
The plugin had never been installed, on any prior run.

- Pinned the plugin the same way as the existing Headlamp UI-plugin pattern:
  `config/dependencies.yaml` (`grafana.plugins.grafana-clickhouse-datasource`, v4.18.0 +
  real sha256), `compose/scripts/fetch-grafana-plugins.py`, `just grafana-plugins` (wired
  into `up` and `up-cloud-kind`), bind-mounted to `GF_PATHS_PLUGINS`
  (`compose/deployment/grafana/plugins/` → `/data/grafana/plugins`, git-ignored contents).
- Two zip-specific gotchas found and fixed in the fetch script (the Headlamp plugins are
  tarballs, this one is a zip):
  - The version-stamp file **must live outside the plugin directory** — Grafana's
    signature check hashes every file under the plugin dir against its signed
    `MANIFEST.txt`; one extra file marks the whole plugin "modified" and unloadable.
  - `zipfile.extractall` does **not** restore Unix permissions (unlike `tarfile`) — the
    backend binaries (`gpx_clickhouse_*`) lost their executable bit and failed with
    `permission denied`. Fixed with an explicit chmod pass from each entry's
    `external_attr`.
- Verified end-to-end: datasource health returns `"Data source is working"`; a live query
  through Grafana → ClickHouse returned real row counts.
- **ADR-0024 corrected** — its "Operational notes" claimed the plugin installed via
  `GF_INSTALL_PLUGINS` and survived restarts from the `lgtm-data` volume; both were wrong.

### 2. Fixed the datasource bug on all "Temporal Critical Flows" dashboards

All five `dashboards-critical/*.json` files hardcoded `uid: prometheus` (lgtm's bundled,
compose-only instance) instead of `uid: prometheus-kind` (the durable store the in-cluster
Prometheus actually remote_writes to on kind). Root cause of "almost every panel is blank."
Fixed across `overview`, `flow-start-signal`, `flow-workflow-progress`,
`flow-task-processing` (later relocated, see below), and `worker-tuning`.

### 3. Reworked the worker/KEDA dashboard

`worker-tuning.json` → retitled **"Temporal Critical Flows — Worker Fleet & KEDA Scaling"**,
restructured into USE rows, all queries verified against live metric names/labels (not
guessed):

- **Saturation** (the Tier-1 canary): workflow/activity schedule-to-start p99 (thresholds
  tightened to the documented 200ms), `temporal_cloud_v1_approximate_backlog_count`,
  `temporal_slot_utilization` (the existing recording rule — was in Prometheus, on no
  dashboard).
- **Utilization** (why it moved): task slots available, poll success rate (workflow, SDK —
  exact; by task type, Cloud — covers activity too since Core SDK has no activity-side
  poll-succeed counter), sync match rate (Cloud only:
  `poll_success_sync_count / poll_success_count`).
- **KEDA Alignment**: worker replicas running by version (`count(up{job="kubernetes-pods"})`
  faceted by `temporal_io_build_id`/`temporal_io_deployment_name` — confirmed exact label
  names live). KEDA's own per-version backlog read (`DescribeWorkerDeploymentVersion`) isn't
  exported to Prometheus today — no `keda_*` metrics are being scraped — so this is a
  resulting-action proxy, not KEDA's live decision input; the dashboard's intro text points
  to Headlamp's KEDA plugin for that (live ScaledObject/HPA status — a complementary, not
  overlapping, view).

### 4. Made the request-path dashboards backend-agnostic; split out what can't be

Investigated whether one dashboard set can serve both Temporal Cloud and self-hosted OSS.
Verdict: partial. Metrics tied to the customer-facing API surface unify cleanly; metrics
below that boundary (persistence, internal task processing, shard, process/goroutines)
never will — Temporal Cloud is a managed control plane and structurally does not expose
them to customers, on any topology (unlike the worker-backlog gap above, which closes once
OSS-on-kind lands).

- **`overview`, `flow-start-signal`, `flow-workflow-progress`**: every RPS/error/latency/
  availability panel now carries two query targets — OSS (`service_requests` etc.,
  `rate()`'d) and Cloud (`temporal_cloud_v1_*`, already a precomputed rate/percentile, never
  `rate()`'d) — sharing one legend. Whichever backend is live populates the panel; verified
  live (Cloud target returned real per-operation series, OSS target cleanly empty, no
  errors). `max by (operation)` used to combine Cloud's precomputed percentiles across any
  extra label dimensions (can't literally sum percentiles).
- **New "Temporal Self-Hosted Internals" Grafana folder** (new provisioning file
  `self-hosted-internals.yaml`, new dir `dashboards-self-hosted-internals/`):
  - `task-processing.json` — relocated `flow-task-processing.json` unchanged (entirely
    OSS-only content: `task_latency`, shard lock, history cache).
  - `server-health.json` — new, consolidating persistence RPS/latency + goroutines +
    restarts that used to be duplicated near-identically across three of the Critical Flows
    dashboards (one "Persistence Latency p99" copy instead of three).
  - `temporal-server-legacy.json` — relocated the old community-imported "Temporal Server
    Metrics" dashboard (`dashboards/temporal-server.json`, schemaVersion 30, `id: 36`)
    wholesale rather than hand-picking it apart; its unique panels (shard rebalancing/
    distribution, memory, GC, per-namespace workflow completion stats) are real but niche,
    and it's entirely OSS-only content regardless.
- **Retired `dashboards/sdk.json`** — the old "Temporal SDK"/RPC-overview dashboard, fully
  superseded by the above and had a pre-existing bug (two panels using an identical query).

## Decisions

- **Dual-target queries, not a datasource-switch variable**, for backend-agnostic panels —
  no manual step to move between Cloud and OSS; both metric families already land in
  `prometheus-kind`.
- **A permanent folder split, not a temporary one.** "Temporal Self-Hosted Internals" is not
  provisional pending some future Cloud feature — persistence/task-processing/process
  metrics are a product boundary of the managed service and will never populate there.
- **Relocate the legacy community dashboard wholesale rather than re-engineer it.** Its
  schemaVersion-30 per-panel `"datasource": null` style briefly appeared not to provision at
  all (turned out to be a stale search-index cache after restart, not a real failure) —
  not worth the risk/cost of rewriting a 3600-line imported dashboard when it's honest,
  correctly-folder-placed OSS-only content either way.

## Open questions / next

- **KEDA's own per-version backlog read is not observable in Grafana today.** Would need
  the KEDA operator's own `/metrics` scraped (check whether its chart already carries
  `prometheus.io/scrape` annotations before adding a new scrape job) to chart
  `keda_scaler_metrics_value` — the actual number the Temporal scaler acted on, not the
  `up{}` replica-count proxy currently in place.
- **Cloud equivalents for `service_requests{operation="AddWorkflowTask"}`-style internal
  lifecycle counters** (workflow/activity task Scheduled/Started/Completed, from the retired
  legacy dashboard) were not verified live — plausible via `temporal_cloud_v1_service_request_count`
  given it already carries internal system RPC names (`DescribeWorkerDeploymentVersion`,
  `RecordWorkerHeartbeat`), but unconfirmed while idle. Would enrich `flow-workflow-progress`
  if verified.
- No load test was run to force schedule-to-start latency or backlog up — all queries were
  validated for correctness/shape against an idle system, not against an actual saturation
  event.
