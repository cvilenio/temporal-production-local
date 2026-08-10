# Temporal Cloud failover readiness (customer OpenMetrics)

Platform-agnostic instructions for building a failover-readiness board from Temporal Cloud
OpenMetrics alone.

Any Prometheus-compatible store that scrapes the Cloud endpoint can host these queries.
The Grafana JSON in this repo is one rendering of the same design, not the contract.

## Goal

Decide whether sync-request latency on the experience APIs justifies failing over a
namespace out of its current source region.

Build as if the namespace carries sustained traffic.
Idle or bursty demo traffic is not a reason to weaken the gate.

## The design in one page

Read this section alone if you only want the design.
Everything after it is implementation detail for expressing the design in PromQL, and none of
it changes what is being measured or why.

### The four signals

| Signal | What it answers | Role |
|---|---|---|
| Latency percentile on the experience APIs | Are callers being made to wait? | **The gate.** Nothing else can turn the verdict red. |
| Request volume on the same APIs | Is there enough traffic for the percentile to mean anything? | Chooses which percentile to trust, or refuses to judge. |
| Any request activity in the namespace | Is telemetry arriving at all? | Separates "healthy" from "we cannot see". |
| Replication lag | How much data would a failover risk? | Context only. Never gates. |

The experience APIs are the three calls a user waits on: `StartWorkflowExecution`,
`SignalWorkflowExecution`, `SignalWithStartWorkflowExecution`.
Everything else a namespace does is background work and does not belong in a failover gate.

### The thresholds

| Question | Threshold | Where it came from |
|---|---|---|
| How slow is too slow? | **0.2s** on the gating percentile | Inherited. Baseline is ~0.02s, so this is 10x normal. |
| Which percentile do we trust? | **p99** at 1000+ requests / 5 min, **p95** at 100-1000, **judge nothing** below 100 | Inherited. At ~100 samples a p99 is just the single worst request. |
| How long must it stay bad? | **5 breaching minutes out of any 10** | Inherited. Not consecutive: intermittent breaches are real breaches. |
| What fires immediately? | **2+ namespaces breaching at once** and a peak of **25x** the ceiling | Inherited. Catches a region-wide event on its first minute. |
| When do we stop trusting the data? | No telemetry for **7 minutes** | Measured here, not inherited. See publication lag. |

Every inherited number came from an internal multi-account scan and cannot be re-derived from
one customer's endpoint.
Treat them as starting points and say so on the board.

### The time periods

| Period | Length | Why |
|---|---|---|
| Sample granularity | 1 minute | The endpoint publishes 1-minute buckets. Nothing finer exists. |
| Sustained window | 10 minutes | The window the 5-of-10 rule is counted over. |
| Liveness window | 7 minutes | Must exceed the ~3.5 min publication lag with margin. |
| Display window | 30 minutes | What the breach table shows. Does not affect the verdict. |
| Lag offset | 4 minutes | Windows end 4 min in the past, because the newest minutes are always empty. |

### The verdict

Five states, and the three non-red ones are **not** interchangeable.

| State | Meaning |
|---|---|
| **FAILOVER?** | Sustained breach, or a severe region-wide one. Act. |
| **WATCH** | A real breach that has not met either bar yet. |
| **CLEAR** | Judged, and healthy. |
| **TOO FEW REQUESTS** | Telemetry is arriving, there is just not enough traffic to judge. Benign. |
| **NO DATA - FEED SILENT** | Nothing is arriving. After a failover this is itself a signal. |

The single rule that matters most: **absence of data and absence of problems must never render
the same way.**
A board that shows "clear" when it simply cannot see is worse than no board.

### What this design deliberately does not do

It does not gate on error rate.
The error metric has no error-class dimension, so a duplicate start or a client cancel is
indistinguishable from a real outage.

It does not gate on throttling or platform overload.
The cause label that would make that meaningful is not published to customers.

It does not confirm recovery in the target region.
It tells you whether the source region is in trouble, not whether the destination is ready.

## Metrics and labels (scrape-verified)

| Concept | Series | Notes |
|---|---|---|
| Experience-API latency | `temporal_cloud_v1_service_latency_p50` / `_p95` / `_p99` | Pre-computed percentiles. Unit: seconds. |
| Request volume | `temporal_cloud_v1_service_request_count` | Already a per-second rate. Unit: requests/s. |
| Requests / 5 min | `avg_over_time(request_count[5m]) * 300` | Convert rate → count over five minutes. |
| Replication lag (context only) | `temporal_cloud_v1_replication_lag_p50` / `_p95` / `_p99` | Unit: seconds. No SLA. |

Labels verified on a live scrape of those series:

- `temporal_namespace`
- `temporal_account`
- `region`
- `operation` (on service latency / request_count)
- plus scrape labels `instance`, `job`

Replication lag carries `region` as the **replica (target)** region, not the source.
Example verified set:

`temporal_cloud_v1_replication_lag_p99{temporal_namespace="…", temporal_account="…", region="aws-us-west-2"}`

## Hard query rules

- Never apply `rate()`, `increase()`, `irate()`, or `histogram_quantile()` to `temporal_cloud_v1_*`.
- Never average or otherwise aggregate a pre-computed percentile across dimensions.
- Use `max()` only when you need a worst-case scalar, and label it as max-of-percentiles.
- Display units must stay seconds for latency and lag.
- Any human-facing count must state per-second vs per-minute (or per 5 min) in the label.

## Experience APIs the gate watches

Regex (PromQL `=~`):

`StartWorkflowExecution|SignalWorkflowExecution|SignalWithStartWorkflowExecution`

Volume-banded gating latency (p99 at high volume, p95 at medium, nothing below the floor).
Wrap the union in `sum without(__name__)` so both arms share one labelset:

```promql
sum without(__name__) ((
  (
    temporal_cloud_v1_service_latency_p99{temporal_namespace=~"$namespace", region=~"$region", operation=~"$exp_ops"}
    and on(temporal_namespace, region, operation)
    (avg_over_time(temporal_cloud_v1_service_request_count{temporal_namespace=~"$namespace", region=~"$region", operation=~"$exp_ops"}[5m]) * 300 >= $p99_min_requests)
  )
  or
  (
    temporal_cloud_v1_service_latency_p95{temporal_namespace=~"$namespace", region=~"$region", operation=~"$exp_ops"}
    and on(temporal_namespace, region, operation)
    (avg_over_time(temporal_cloud_v1_service_request_count{temporal_namespace=~"$namespace", region=~"$region", operation=~"$exp_ops"}[5m]) * 300 >= $min_requests)
  )
))
```

Below `$min_requests` neither arm matches.
That absence is **not judged**, not clear.

Without the wrap, a mid-window volume cross of `$p99_min_requests` can put both arms on the same series inside a range subquery.
`count_over_time` / `max_over_time` drop `__name__`, the two collapse to one labelset, and the query aborts with `vector cannot contain metrics with the same labelset`.
Ordinary diurnal traffic variation is enough to trigger it.
A broken query must not render as NO DATA - FEED SILENT.

## Measured publication lag

Live scrape on this surface measured endpoint publication lag ≈ **209 seconds (~3.5 min)**.

Consequences:

- Liveness grace must clear that lag with margin (default **7** minutes here; a 3-minute window is permanently empty).
- Windowed subquery counts and lagged instant terms (watch / severity / gating-presence) need an offset past the lag (default **4** minutes here).
- Do **not** offset the liveness check itself.
  Liveness must see whether data is arriving at all, lag included.

## Subquery traps (measured)

Computed gating expressions cannot take a raw range selector.
Only a subquery is legal.

A subquery with no explicit resolution (`[10m:]`) inherits a 30s step and double-counts ~1/min samples
(measured on a live series: raw-range ground truth vs `[10m:]` ≈ 3× inflation).

Use `[Nm:1m] offset ${publication_lag_min}m` for windowed breach/total steps.

### Volume-banded p99 or p95 union (labelset collapse)

A volume-banded `p99 or p95` union must be wrapped in `sum without(__name__) (...)`.
Inside a range, both arms can appear for the same series when volume crosses the p99 floor mid-window.
Once `__name__` is dropped, the two collapse to one labelset and Prometheus aborts the whole query.
Ordinary diurnal traffic variation is enough.
Apply the wrap at every site the gating expression appears (verdict and breach-table windowed/peak refs).

On **continuous** traffic (one sample per minute), those steps equal real minutes and a duty ratio is exact.

On a **gapped** series, carry-forward inflates the numerator when a gap follows a breaching sample.

Worked example: breach at minute 0, gap through minute 4, healthy 5–9 → BREACH_STEPS 5 / TOTAL_STEPS 10 = 0.50 from one real breaching minute.

Accepted edge case: breach immediately followed by source-region silence (a real failover signature) briefly over-scores, then liveness flips to feed-silent.

Durable production fix: a Prometheus recording rule that materialises the breach boolean at scrape time so windows can use raw ranges on selectors.

## Sustained path (duty cycle)

```text
DUTY = BREACH_STEPS / TOTAL_STEPS
HONEST_SAMPLES = count_over_time(p99_selector[window] offset lag)   # raw range on a selector
```

Sustained fires when both hold:

- `DUTY >= ($sustain_min / $sustain_window_min)`
- `HONEST_SAMPLES >= $sustain_min`

Combine conditions by **multiplying bool terms**, never with `and on()`.

`and` returns the left value and tests only the right's presence, which silently disables guards.

## Severity path

Breadth: count of namespaces currently breaching the ceiling (lagged).

Magnitude: peak gating latency across **all** gating series in the region (lagged).

These are deliberately not coupled per-namespace (reference region-wide-event signature).

Consequence: one namespace at 25× plus one at 1.05× can satisfy the path.

With `$breadth_min = 2`, severity is **inert on a single-namespace view**.
There the sustained path is the only route to a red failover verdict.
That is the same rule as the single-namespace default below — one limit, two places it shows up.

## Liveness (correction vs a latency-only check)

Liveness rides the namespace's full `temporal_cloud_v1_service_request_count` stream in the selected region.
No operation filter.

A quiet experience API is not a silent region.

Measured failure mode of a p99-only check: 0 experience-API latency series vs 7 request_count series in the same 7-minute window.

**One horizon for score and feed-silent:** multiply SCORE by the liveness indicator

`sum(count_over_time(request_count[stale_grace])) >= bool 1`

so SCORE disappears exactly when liveness does.
Do not maintain a second offset/lookback on the gating-presence term that can outlive `$stale_grace_min` and paint CLEAR during silence.

Verdict states that must stay distinct:

- Judged healthy → CLEAR
- Feed flowing, not enough to judge → TOO FEW REQUESTS
- Nothing emitting in namespace+region → NO DATA - FEED SILENT

## Namespace scope and liveness masking

Default the namespace control to a **single** namespace with traffic.
A failover decision is about one namespace at a time.

Multi-select remains available so the severity breadth path can see multiple namespaces when scope is deliberately widened.

When more than one namespace is selected, liveness sums `service_request_count` across the selection.
A busy namespace can mask a silent one, so a namespace that has actually failed over may render TOO FEW REQUESTS instead of NO DATA - FEED SILENT.

Judge one namespace at a time for a failover decision.

With that single-namespace default, `$breadth_min = 2` makes severity inert and the sustained path is the only route to red — consistent with the severity-path note above.

## Replication lag label trap

Scope replication-lag panels by `temporal_namespace` only.

Put `region` in the legend as “lag into \<region\>”.

Filtering lag by the Decision-row **source** region renders a healthy replica series empty (false no-data).

Lag is context for a failover decision, not a gate.
It is Temporal-owned and carries no SLA.

## What cannot be built on this surface

| Omitted | Why |
|---|---|
| Success-ratio panels | `service_error_count` has no error-class dimension; client cancels and duplicate starts cannot be separated. |
| Platform overload / throttle cause | `resource_exhausted_cause` does not exist on the customer OpenMetrics catalog. |
| Nexus metrics | Wrong input to “fail over *this* namespace”. |
| Confirm recovery in the target region | A source-scoped board cannot; lag is replica-labelled context only. |

## Inherited calibration (cannot re-derive from one account)

Defaults such as `$lat_ceiling_s=0.2`, `$sustain_min/window=5/10`, `$sev_multiple=25`, `$breadth_min=2`, and the volume floors come from an internal multi-account scan.

State on the board that they are inherited and unvalidated on any single customer surface.

The sustained path's false-alarm rate has never been measured.

## Reference rendering in this repo

Grafana folder **Temporal Cloud Failover Readiness**.

Files: `compose/observability/grafana/dashboards-failover/`, provisioned via `failover.yaml`.

Datasource uid: `prometheus-kind`.
