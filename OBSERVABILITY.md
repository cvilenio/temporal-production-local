# Observability

> **Status (read first).** Metrics on kind are wired and live-verified: the in-cluster
> Prometheus (`prometheus-kind`) scrapes both **worker SDK pods** (Worker Deployment) and the
> **Temporal Cloud OpenMetrics endpoint** (`metrics.temporal.io`), and the **Temporal Critical
> Flows** Grafana folder (backend-agnostic Critical Flows/Worker Fleet/Durable Execution Value
> dashboards) is confirmed rendering real data — see `ai_checkpoints/0026` and `0027`. The
> worker scrape targets below (`orders-*-worker:9000`) and the `temporal:9091` server target
> describe the **historical, no-longer-running legacy Compose-OSS topology** — kept for
> reference on that path, not the current kind + Cloud one.

The stack uses `grafana/otel-lgtm` — a single container bundling OpenTelemetry Collector, Prometheus,
Tempo, Loki, and Grafana with all datasources pre-wired.  Just run `docker compose up` and open Grafana.

```
http://localhost:3000   Grafana  (admin / admin)
```

---

## Dashboards

| Dashboard | What it shows |
|---|---|
| **Temporal Server** | Server health: service requests, persistence latency, shard stats, GC, goroutines |
| **Temporal SDK** | Per-namespace SDK metrics: workflow completion rates, activity latency, task-queue poll stats |

These are pre-loaded from `compose/observability/grafana/dashboards/` (folder **Ziggymart Demo**).

A second set — the **Temporal Critical Flows** folder — gives a drill-down view of the
critical-path server operations on this local OSS + Postgres stack. See
[Critical Flows dashboards](#temporal-critical-flows) below.

---

## Two-pipeline model

Temporal demos need two distinct metric transports.  Here is why, and which to use where:

| Pipeline | Transport | What goes here | Why |
|---|---|---|---|
| **PULL** | Prometheus scrapes `/metrics` on each process | Temporal server metrics, SDK runtime metrics (`temporal_workflow_*`, `temporal_activity_*`), custom metrics via `metric_meter()` | Dashboard name conventions match only with Prometheus scrape + correct suffixes. Replay-safe inside workflows. |
| **PUSH** | OTLP gRPC → OTel Collector → Prometheus | Business metrics from activities + the API (e.g. `orders.payments_captured_total`) | Workflows run in a sandbox where only the SDK meter is safe; activities are unrestricted. |

Traces always go PUSH (OTLP → Tempo). **Logs are separate** (ADR-0018): every service emits
structured JSON to **stdout**; on kind a **Grafana Alloy DaemonSet** tails pod stdout and ships
to Loki (app→stdout→node agent→backend, the production pattern), while host-plane services
(`mock-api`) push OTLP straight to lgtm. See the **Logging** section below.

---

## How to add telemetry

### In a workflow (`@workflow.defn`)

```python
from temporalio import workflow
from temporalio.contrib.opentelemetry.workflow import completed_span as otel_span

@workflow.defn
class MyWorkflow:
    def __init__(self):
        # Custom metrics — ride the Temporal SDK pull pipeline (replay-safe).
        # Tag with BOUNDED labels only — not order_id / trace_id / amounts.
        meter = workflow.metric_meter()
        self.steps_counter = meter.create_counter(
            "my_workflow_steps_completed",
            description="Steps completed in MyWorkflow",
        )

    @workflow.run
    async def run(self, input):
        # Structured log — skipped during replay by default (log_during_replay=False)
        workflow.logger.info("step starting", extra={"step": "my_step"})

        # Custom OTel span — only emitted if TracingInterceptor is wired AND
        # there is a parent span (i.e. client started a trace).
        # Import is safe: temporalio.contrib is fully pass-through in the sandbox.
        otel_span("my_workflow.my_step", attributes={"step": "my_step"})

        # Custom counter
        self.steps_counter.add(1, {"step": "my_step"})
```

### In an activity (`@activity.defn`)

```python
from temporalio import activity
from shared.metrics import business_meter

@activity.defn
async def my_activity(req):
    # Structured log — includes activity context automatically
    activity.logger.info("processing", extra={"item": req.item_id})

    # Operational metric — SDK pull pipeline (PrometheusConfig endpoint)
    sdk_meter = activity.metric_meter()
    latency = sdk_meter.create_histogram_timedelta(
        "my_activity_duration",
        description="Wall-clock time for my_activity",
        unit="duration",
    )

    # Business metric — OTLP push pipeline
    meter = business_meter()
    counter = meter.create_counter("orders.items_processed_total")
    counter.add(1, {"status": "success"})
```

### In the API service (`main.py`)

```python
from shared.metrics import business_meter

async def submit_order(...):
    meter = business_meter()
    orders_started = meter.create_counter("orders.started_total")
    orders_started.add(1)
```

> **Replay rule** — Workflow code MUST use `workflow.metric_meter()` (replay-safe, suppresses during
> replay).  Never use `business_meter()` inside a workflow.

---

## How server metrics are enabled

The Temporal server (`temporalio/server`) exposes combined Prometheus metrics when the
`PROMETHEUS_ENDPOINT` env var is set.  In this repo, `docker-compose.yml` sets:

```yaml
temporal:
  environment:
    - PROMETHEUS_ENDPOINT=0.0.0.0:9091
```

Prometheus inside `lgtm` scrapes `temporal:9091/metrics` via `compose/observability/prometheus/prometheus.yaml`.

---

## How SDK metrics are pulled

Each Python process binds a `/metrics` HTTP endpoint via `PrometheusConfig` in `shared/telemetry.py`.
The endpoint listens on `0.0.0.0:9000` (configurable via `SDK_METRICS_PORT` env).  Prometheus inside
`lgtm` scrapes all three app containers:

```yaml
- job_name: temporal-sdk
  static_configs:
    - targets:
        - 'orders-service:9000'
        - 'orders-workflow-worker:9000'
        - 'orders-activity-worker:9000'
```

`durations_as_seconds=True` makes latency histogram **values** second-scale (vs the SDK default of
milliseconds) — it does **not** add a `_seconds` name suffix. The name suffix is a *separate* exporter
option (`unit_suffix`, default **off**, not set here), so histograms are exposed as bare
`temporal_*_latency_bucket` with second-scale values (verified on the live Python worker). This applies
to **custom** histograms via `activity.metric_meter()`/`workflow.metric_meter()` too — same pull
exporter — so `orders_payment_capture_duration` → `orders_payment_capture_duration_bucket` (no `_seconds`).
Any `*_seconds_bucket` series still visible in Prometheus are **stale leftovers** in the TSDB from an
earlier run and carry no current data. The **OTLP push** path is separate: the collector appends `_total`
to counters and the unit to histograms (e.g. `orders_payment_amount_usd_cents_bucket`).

> **Counter naming gotcha.** The Temporal **Python** SDK's Prometheus exporter exposes counters
> **without** a `_total` suffix (e.g. `temporal_workflow_completed`, `order_workflow_steps_completed`).
> `counters_total_suffix=True` is a no-op in this SDK build, so we don't set it, and the bundled
> `sdk.json` has been aligned to the no-`_total` names. By contrast, counters sent over **OTLP**
> (business metrics) **do** get `_total` appended by the collector's Prometheus exporter — so business
> counters are queried as `orders_payments_captured_total`. Two pipelines, two conventions.

---

## SDK metrics across languages (runtime-verified: Python only)

Temporal SDKs expose **the same metric names and families** — the cross-language differences are
**naming conventions** and **one optional adapter**, *not* which metrics exist. (Checked against SDK
source; runtime behaviour here verified on the **Python** SDK only — other-language workers aren't
running yet.)

**Same families everywhere.** sdk-core (Rust) defines `workflow_completed`, `workflow_canceled`,
`workflow_continue_as_new`, `workflow_failed`, `workflow_endtoend_latency`, `activity_execution_failed`
(tagged with `activity_type`), etc. — the *same* names the Tally-based SDKs use. A Prometheus **counter
series only appears once it is first incremented**, so panels for `workflow_canceled` /
`continue_as_new` / `activity_execution_failed` read empty in a healthy happy-path run and light up when
those events happen. Verified on this stack: `temporal_workflow_completed` and
`temporal_activity_execution_failed` were initially absent (workflows were *failing*, activities weren't),
then appeared (135 / 26) once workflows completed and the flaky scenario failed activities.

> **Earlier mistake, corrected:** these counters were briefly assumed "not emitted by the core SDK." They
> *are* emitted — they were just zero-valued (hence unexported) during a window when every workflow was
> failing. `sdk.json` queries the **direct** metric names (no derived substitutes needed).

**What actually differs — naming conventions:**

| | sdk-core (Python, .NET, Ruby) | Tally + Prometheus naming (Java native; Go via opt-in `contrib/tally`) |
|---|---|---|
| `_total` on counters | no (`counters_total_suffix` default off) | **yes** (`NewPrometheusNamingScope` appends it) |
| `_seconds` unit suffix on latencies | no (`unit_suffix` default off); value still seconds via `durations_as_seconds` | **yes** |
| metric prefix | configurable | configurable |

**SDK architecture (per SDK source):**

- **Java** — Tally (`com.uber.m3.tally`) natively.
- **Go** — its *own* `metrics.Handler`; Tally is an **optional** adapter (`contrib/tally`), opt-in by the user.
- **Python / .NET / Ruby** — wrap **sdk-core** (Rust).
- **PHP** — pure PHP, separate (not sdk-core).

> ⚠️ **Verified on Python (this stack) only.** The sdk-core row is *expected* to hold for .NET/Ruby
> (shared core) but is untested here; Java/Go differ per the table. Always confirm against the live
> `/metrics` when a new-language worker is added. Server metrics (bare names) are a separate convention —
> see the **Temporal Critical Flows** section.

### One-dashboard-fits-all (future multi-language)

Because the metric *families and names* line up across SDKs, these SDK panels are largely **portable as-is** —
no per-language dashboards needed. The one gap to handle when non-Python workers are added is the **suffix
divergence**: a Tally Java/Go worker exposes `temporal_workflow_completed_total` and
`temporal_*_latency_seconds_bucket`, which the current bare-name queries won't match. Handle it **once at
the edge** — e.g. `metric_relabel_configs` to strip `_total`/`_seconds` at scrape time, or `<query> or
<query-with-suffixes>` fallbacks — rather than forking the dashboards per language. *(Not implemented now;
Python is the only worker today.)*

---

## Dashboards (bundled, provisioned)

| Dashboard | Source | Notes |
|---|---|---|
| **Temporal Server** | samples-server `temporal.json` | Server-side Go metrics — match the same Temporal server |
| **Temporal SDK** | samples-server `sdk.json` (patched) | `_total` suffixes stripped to match the Python SDK's pull names |
| **Ziggymart — Business & Custom Metrics** | `business.json` (hand-built) | OTLP push business metrics + custom workflow counters; guaranteed to match this app |

---

## Temporal Critical Flows

The **Temporal Critical Flows** Grafana folder is a drill-down view of the critical-path server
operations — the three flows that most directly affect application health — plus a worker-side view.

Files: `compose/observability/grafana/dashboards-critical/`, provisioned via `critical.yaml`.

| Dashboard | What it shows |
|---|---|
| **Overview** | Critical frontend RPS / errors / **p99 latency with threshold lines** (200ms/1s) by operation; history task-processing p99; persistence p99; availability, goroutines, restarts, instances up. Cross-links to the flows. |
| **Starting & Signaling** | Flow 1 — `Start*`/`Signal*` RPS, errors, p99 latency (threshold lines); hot-path persistence. |
| **Workflow Progress** | Flow 2 — `Respond*` and `Poll*` RPS/errors/latency; progress persistence. |
| **Task Processing** | Flow 3 — TransferActive vs TimerActive **split** (p50 + p99 + tasks/sec); ShardInfo lock + history-cache latency; task persistence. |
| **Worker Tuning** | Worker SDK levers: task slots available, sticky-cache hit/miss/size, pollers, schedule-to-start p99, poll success vs empty. |
| **Durable Execution Value** | Cloud only. Workflow/Activity success rates (account + per-namespace), Activity Result outcomes, retries-absorbed-by-Temporal derivation (`activity_task_fail_count` − `activity_fail_count`) with an approximate "success rate without retries" and "9's gained" story, and Workflow runtime (schedule-to-close) by type. |

### OSS + Postgres scope

This is OSS Temporal with a single **Postgres** backend. A few things a richer setup might show have
**no local equivalent** here and are intentionally omitted (each is noted in-dashboard so the gap is
explicit):

| Omitted | Why dropped / how collapsed |
|---|---|
| `ALERTS{firing,critical}` | No AlertManager wired locally |
| Pod CPU % of request | No Kubernetes (uses `process`/Go-runtime + `restarts` instead) |
| Dedicated storage-engine panels | On Postgres, storage collapses to the **persistence API layer** (`persistence_latency_bucket` by operation) |
| Per-namespace RPS/latency | `perNamespaceScope` not set → no `namespace` label on server metrics |
| Worker-excluded `Respond*` latency | OSS `service_latency` for `Respond*` **includes worker time** (noted on the panel) |

> **Metric naming (verified against the running stack).** Server panels use **bare names**
> (`service_requests`, `service_latency_bucket`, `task_latency_bucket`, `persistence_latency_bucket`,
> `lock_latency_bucket{operation="ShardInfo"}`, `cache_latency_bucket{operation="HistoryCacheGetOrCreate"}`)
> — no `temporal_` prefix, matching this repo's server config. Notes from verification:
> - **No `cluster` label** is emitted locally, so these dashboards do **not** filter by cluster. There
>   is no `temporal_service_type` label on the history `lock`/`cache` metrics either — only `operation`.
> - Worker Tuning panels use `temporal_`-prefixed SDK names with **no `_total`** on counters and bare
>   `*_latency_bucket` (no `_seconds` — sdk-core default `unit_suffix` is off; see the SDK-metrics section above).
> - Error panels use `OR on() vector(0)` so they render a flat **0** line in a healthy system rather than
>   "No data" (`service_errors` series only appear once an error fires).
>
> Every panel was confirmed returning live data under order load. To re-verify: `localhost:9090` isn't
> published — query Prometheus through Grafana's datasource proxy
> (`POST localhost:3000/api/datasources/proxy/uid/prometheus/api/v1/query`).

---

## Explore in Grafana

1. **Metrics** → Explore → Prometheus datasource
   - `temporal_workflow_completed` — SDK counter (pull, **no** `_total`), scraped from workers
   - `order_workflow_steps_completed` — custom counter from `workflow.metric_meter()` (pull, no `_total`)
   - `orders_payments_captured_total` — business counter pushed via OTLP (`_total` added by the collector)

2. **Traces** → Explore → Tempo datasource
   - Search by service name: `orders-service`, `orders-worker-workflow`, `orders-worker-activity`
   - Each order creates a trace spanning `StartWorkflow → RunWorkflow → activity` spans, plus the
     custom `order.*` spans added in `order_workflow.py`

3. **Logs** → Explore → Loki datasource
   - On kind, filter by the **agent-attached** labels: `{k8s_pod_name=~"orders-activity.*"}` or
     `{k8s_namespace_name="orders"}` (proof the line came through Alloy, not an app push). Host-plane
     services keep `{service_name="mock-api"}` from their OTLP resource.
   - Every line is the app's structured JSON (level/logger/trace_id/order_id parsed by Alloy's
     `stage.json`), so you can also filter on `| json | level="error"`.

Correlate all three: copy a `trace_id` from a Loki log line and open it in Tempo.

## Logging (ADR-0018)

One shared kernel, `obslog` (`libs/logging/python/`, `structlog`-based), gives every service the
same JSON schema and a type-robust "log codec" (never raises; `repr()` fallback). Two sinks:

- **stdout JSON — always.** Visible in `kubectl logs` / Headlamp / Docker Desktop. This is the
  k8s log contract and what the node agent collects.
- **Backend** — on **kind** the **Alloy DaemonSet** (`deploy/charts/alloy`) tails `/var/log/pods`,
  attaches `k8s_*` metadata, and ships to host-side Loki (apps set `LOG_OTLP_PUSH=false`). On the
  **host plane**, `mock-api` pushes OTLP to lgtm directly (no node agent there).

**Replay-safety boundary:** workflows keep `workflow.logger` + `wf_log_extra()` (deterministic,
no contextvars); activities and plain async code use `activity.logger` / `obslog.get_logger()`
with the concurrency-safe `obslog.bound()` context manager. See ADR-0018 for the full schema,
the "observability is a separate durable tier" framing, and the polyglot extension path.

> **Note on uvicorn access logs.** `obslog.init_logging` owns the root logger, so app logs (and
> most library logs that propagate) render in the shared schema. uvicorn's access/error loggers
> set `propagate=False`; point them at the root or attach the formatter to capture HTTP access
> lines too.
