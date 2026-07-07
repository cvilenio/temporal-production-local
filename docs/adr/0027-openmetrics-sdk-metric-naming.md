# ADR-0027: Adopt OpenMetrics SDK-metric naming as the cross-SDK contract

- **Status:** Accepted (histogram parity landed; counter `_total` coupled upstream)
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
   - **Python (today):** `PrometheusConfig(unit_suffix=True, durations_as_seconds=True)` in
     `appkit.telemetry.init_observability`. `counters_total_suffix` is **not** set — see
     coupled counter adoption below.
   - **Java:** Micrometer default (no extra config expected) — already emits `_total` on
     counters.
   - **Go:** `NewPrometheusNamingScope`.

3. **Grafana SDK-metric dashboards** query the names actually emitted on `:9000`.
   Histogram panels use `*_seconds_*` (parity with Java/Go).
   Counter panels use bare names until the coupled counter change lands.
   Recording rules over gauge inputs (e.g. `temporal_slot_utilization`) are unchanged.

4. **Counter `_total` adoption is a COUPLED change, gated on upstream.**
   On `temporalio==1.30.0`, `counters_total_suffix=True` is a no-op — counters remain bare
   despite the Python binding passing the flag through to sdk-core.
   We do **not** set the toggle while it has no effect: a future SDK release that starts
   honoring it would silently append `_total` and break bare-name counter dashboard panels on
   an unrelated version bump.
   When a Python SDK release honors `counters_total_suffix` (or this repo moves SDK metrics
   to OTLP with OpenMetrics naming), land **in the same PR**:
   - flip `counters_total_suffix=True` in `telemetry.py`, and
   - migrate every counter panel in SDK-metric dashboards to `_total` names.
   Do **not** work around the gap with scrape-time relabeling, and do **not** degrade the Java
   exporter to bare counter names.
   Until then, counters **diverge across SDKs by name** (Java `_total`, Python bare) — an
   upstream limitation we accept deliberately.
   Histograms (`_seconds`) are at full cross-SDK parity now.

## Verification (Python 1.30.0)

Live `:9000/metrics` after enabling `unit_suffix` + `durations_as_seconds`:

- `unit_suffix=True` **works** — histograms emit `*_seconds_bucket` / `_sum` / `_count`.
- `counters_total_suffix` **not enabled** — would be a no-op in this release; counters stay
  bare (e.g. `temporal_workflow_task_queue_poll_succeed`).

## Consequences

- Polyglot histogram parity is explicit and live; Java/Go/Python share `*_seconds_*` queries.
- Counter dashboard queries stay on bare Python names until the coupled PR above.
- Java polyglot workers already emit `_total` on counters — document the divergence in runbooks
  until Python catches up.
