# 0027 — Provisioned Capacity, Workflow Outcomes, and Durable Execution Value dashboards

- **Status:** Landed + verified live (kind + Cloud path). Every new panel's PromQL was run
  against the live Prometheus store (`localhost:9009`) and, for the trickiest expression,
  through Grafana's own datasource proxy (`/api/ds/query`) to confirm it's Grafana-safe, not
  just Prometheus-valid. No live workflow executions needed — read-only metrics work
  throughout.
- **Date:** 2026-07-02
- **Builds on:** checkpoint 0026 (fixed the datasource bug + built the backend-agnostic
  Critical Flows dashboards this session extends), ADR-0024 (push metrics to ClickHouse /
  Cloud OpenMetrics scrape).

## Why

Two gaps in dashboard coverage, both closeable from the public Cloud OpenMetrics endpoint
alone (`temporal_cloud_v1_*`, `docs.temporal.io/cloud/metrics/openmetrics`):

1. **Provisioned Capacity / TRU visibility.** No panel showed whether a Namespace is running
   On-Demand or Provisioned capacity, or how usage tracks against APS/RPS/OPS limits.
2. **Durable-execution business value.** No panel answered "is Temporal actually helping my
   workflows succeed where they otherwise couldn't" — the value of Activity retries, not just
   whether the platform is healthy.

## Done this session

### 1. Cloud Capacity section (`dashboards-critical/overview.json`)

New row: Provisioned-TRUs detector, APS/RPS/OPS vs limit, APS utilization %, action
throttling. The provisioned-capacity detector is one trick: `action_limit` and
`action_on_demand_envelope_limit` are identical unless a Namespace is provisioned (the
envelope metric tracks "what the limit would be under on-demand"), so `!=` between them is
a live provisioned/on-demand test. Verified live: this account's `ziggymart` namespace
returns `action_limit == action_on_demand_envelope_limit == 500` — confirmed on-demand, not
provisioned.

### 2. Workflow Outcomes & Health section (`dashboards-critical/flow-workflow-progress.json`)

New row: workflow outcome rates (success/failed/timeout/terminated/continued-as-new), open
workflow count, resource-exhausted errors, cross-region replication lag (p50/p95/p99). All
sourced from the public `temporal_cloud_v1_*` catalog — no new scrape config needed, since
the OpenMetrics endpoint is already wired in (checkpoint 0026).

### 3. New dashboard: "Durable Execution Value" (`dashboards-critical/durable-execution-value.json`)

Answers three business-level questions using only `temporal_cloud_v1_*`:

- **Are my business processes succeeding?** Workflow/Activity success rate (account-wide +
  per-namespace).
- **Is Temporal helping where it otherwise couldn't?** The core derivation:
  `temporal_cloud_v1_activity_fail_count` counts only *final* Activity failures (retries
  exhausted); `temporal_cloud_v1_activity_task_fail_count` counts *every failed attempt*,
  including ones later retried into a success. Their difference is exactly "retries
  absorbed by Temporal." Built into: retries-per-successful-workflow, an approximate
  "success rate without retries," and a "9's gained" stat (`-log10(1-rate)` difference) to
  express that as additional nines of reliability.
- **How long are my business processes taking?** Workflow schedule-to-close latency
  (p50/p95/p99) by workflow type.

The `activity_fail_count` vs `activity_task_fail_count` distinction isn't obvious from the
Cloud metrics reference alone (both are described only as "Activity failures per second" /
"Activity task failures per second") — confirmed against Temporal Server release notes
(`activity_fail`: "Number of final failures for activities") and community support
discussion confirming the task-level counter increments per attempt, including attempts
that later succeed.

### 4. Honest caveats, baked into the dashboard itself

- The "success rate without retries" and "9's gained" panels are explicitly labeled
  approximations — they model each absorbed retry as one additional Workflow failure, which
  isn't precisely true (a Workflow can have many Activities/retries, or recover via other
  paths).
- Explicitly noted what this **can't** measure: Workflow-level crash-protection value
  (Temporal recovering a Workflow after a Worker/process crash) has no OpenMetrics proxy
  today — Workflow Task retries reflect Sticky-cache evictions too, not just crashes, so
  they're not a clean substitute either.
- Most new panels currently show "No data" — this demo namespace has no real completed-
  workflow traffic right now (confirmed: `workflow_success_count` has no series). That's
  correct behavior given the data, not a bug; verified the queries resolve cleanly to empty
  rather than erroring (including through Grafana's `/api/ds/query`, not just raw
  Prometheus).

### 5. Process fix: `AGENTS.md` — waiting for `up-cloud-kind`/`platform-up`

Separately, hit a real problem this session: backgrounded `just up-cloud-kind` produced an
empty `.output` file for a long stretch, and polling/tailing it gave no signal on whether it
was stuck or just quiet (it shells out to `docker compose`/`kind`/Terraform steps that
legitimately buffer). Added guidance to wait for the task's own completion notification (or
`TaskOutput` with `block: true`) instead of polling a quiet log, and to validate readiness
against the same health signals a human would trust (`:8086/healthz`, `:3000/api/health`,
`docker ps` for `(healthy)`, `kubectl get pods -A`) rather than inferring from stdout.

### 6. Status corrections (README.md, OBSERVABILITY.md)

Both files still said kind metrics/observability were "not wired / unproven," predating
checkpoint 0026's fixes. Flipped the README status-table row, the Compose-caveat note, the
doc-pointer line, and `OBSERVABILITY.md`'s own top banner to ✅ — all now point at 0026 and
this checkpoint as the live verification, and clarify that the `orders-*-worker:9000`/
`temporal:9091` scrape targets documented lower in `OBSERVABILITY.md` describe the retired
Compose-OSS topology, not the current kind + Cloud one.

## Decisions

- **New dashboard file, not more rows on `overview.json`.** Durable Execution Value is a
  different audience/purpose (business-value story, not critical-path health) from the
  existing Critical Flows dashboards — kept as its own file in the same folder/tag group so
  it shows up in the same dropdown without diluting the operational-health dashboards.
- **Cloud-only panels get an explicit "no OSS target wired" note**, following the precedent
  set in 0026's worker-tuning dashboard, rather than silently omitting an OSS side that
  doesn't cleanly exist for these metrics (workflow outcome counters, TRUs, durable-execution
  math are all Cloud/billing concepts).
- **Every new metric traces to the public OpenMetrics catalog**, not to anything gated to
  Temporal staff — the bar for inclusion was "documented `temporal_cloud_v1_*` metric a
  customer account can already query," not "looks useful."

## Open questions / next

- All new panels are validated for query correctness against an idle namespace, not against
  real completed-workflow traffic. To see non-empty numbers (and sanity-check the "9's
  gained" math against a real ratio), would need to run actual workflows to completion
  against Cloud — not done this session, would need sign-off per the live-Cloud-testing
  budget rule first.
- The "success rate without activity retries" and "9's gained" panels are intentionally
  rough; worth a follow-up pass if Temporal ships a more precise first-party surface for this
  kind of analysis in the future.
