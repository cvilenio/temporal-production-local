# ADR-0027: Adopt OpenMetrics SDK-metric naming as the cross-SDK contract

- **Status:** Accepted (partial — see Python counter suffix gap below)
- **Date:** 2026-07-07
- **Related:** ADR-0024 (push/business metrics path — separate pipeline, different naming
  rules). Temporal features#607 ("Standardize metrics across SDK languages"). ADR-0021
  (Prometheus pull pipeline for operational metrics).

## Context

Temporal SDKs emit the same *semantic* metric names across languages (`MetricsType` in Java,
sdk-rust for Core-based SDKs, sdk-go for Go).
What differs between SDKs is only the exporter's OpenMetrics suffixing: `_total` on counters,
`_seconds` (and similar) unit suffixes on histograms, and ms-vs-seconds duration values.

Temporal's position (features#607) is that cross-SDK parity requires non-default exporter
settings because of backwards compatibility — an exercise in documenting how to get each SDK
to emit consistent metrics with each other.

We choose the **OpenMetrics standard** (with the suffixes), not bare names, because:

- It is what Micrometer (Java), Prometheus remote-write, and most of the ecosystem expect.
- Java emits it natively (Micrometer default); Go opts in via `NewPrometheusNamingScope`; Core
  SDKs expose first-class toggles.
- A customer going polyglot sets one documented per-SDK toggle and everything lines up — no
  scrape-time relabeling, no per-SDK dashboard forks.

This ADR is the contract every future polyglot worker in this repo conforms to.

**Not in scope:** OSS server metrics (`service_*`), Temporal Cloud OpenMetrics
(`temporal_cloud_v1_*`), and the Go autoscaler client (`temporal_worker_autoscaler_*`) — each
has its own exporter.

**Distinct from ADR-0024:** business metrics pushed via OTLP get `_total` from the collector's
Prometheus exporter on a separate path.
That does not change SDK pull metrics on `:9000`.

## Decision

1. **Cross-SDK SDK-metric naming convention is OpenMetrics:**
   - Counters: `_total` suffix (e.g. `temporal_workflow_completed_total`).
   - Duration histograms: `_seconds` unit suffix before the Prometheus part
     (e.g. `temporal_activity_schedule_to_start_latency_seconds_bucket`).
   - Duration values: seconds (floating point).
   - Gauges: unchanged (e.g. `temporal_worker_task_slots_available`, `temporal_num_pollers`).

2. **Each SDK configures its own exporter to emit the convention:**
   - **Python:** `PrometheusConfig(counters_total_suffix=True, unit_suffix=True,
     durations_as_seconds=True)` in `appkit.telemetry.init_observability`.
   - **Java:** Micrometer default (no extra config expected).
   - **Go:** `NewPrometheusNamingScope`.

3. **Grafana SDK-metric dashboards** query the suffixed names emitted on `:9000`.
   Recording rules over gauge inputs (e.g. `temporal_slot_utilization`) are unchanged.

## Verification note (Python 1.30.0)

On `temporalio==1.30.0`, live `:9000/metrics` after enabling both toggles shows:

- `unit_suffix=True` **works** — histograms emit `*_seconds_bucket` / `_sum` / `_count`.
- `counters_total_suffix=True` **does not** — counters remain bare
  (e.g. `temporal_workflow_task_queue_poll_succeed`, not `..._succeed_total`).

The Python binding passes the flag through to sdk-core; the exporter does not suffix counters
in this release.
No newer patch exists within the pinned `>=1.30,<1.31` range at the time of this ADR.
Dashboard counter panels keep bare names until a fixed SDK lands; histogram panels use
`*_seconds_*`.
Do **not** paper over this with scrape-time relabeling.

## Consequences

- Polyglot metric parity is explicit and documented; Java/Go workers align with minimal config.
- Python histogram dashboards and alerts must use `*_seconds_*` series names.
- Counter `_total` parity is blocked on a Python SDK fix — track upstream and bump the pin when
  a release honors `counters_total_suffix`.
