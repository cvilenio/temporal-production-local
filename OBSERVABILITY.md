# Observability

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

Both dashboards are pre-loaded from `compose/observability/grafana/dashboards/`.

---

## Two-pipeline model

Temporal demos need two distinct metric transports.  Here is why, and which to use where:

| Pipeline | Transport | What goes here | Why |
|---|---|---|---|
| **PULL** | Prometheus scrapes `/metrics` on each process | Temporal server metrics, SDK runtime metrics (`temporal_workflow_*`, `temporal_activity_*`), custom metrics via `metric_meter()` | Dashboard name conventions match only with Prometheus scrape + correct suffixes. Replay-safe inside workflows. |
| **PUSH** | OTLP gRPC → OTel Collector → Prometheus | Business metrics from activities + the API (e.g. `orders.payments_captured_total`) | Workflows run in a sandbox where only the SDK meter is safe; activities are unrestricted. |

Traces and logs always go PUSH (OTLP → Tempo / Loki).

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

`durations_as_seconds=True` exposes latency histograms as `*_seconds`, matching the `sdk.json`
histogram panels.

> **Counter naming gotcha.** The Temporal **Python** SDK's Prometheus exporter exposes counters
> **without** a `_total` suffix (e.g. `temporal_workflow_completed`, `order_workflow_steps_completed`).
> `counters_total_suffix=True` is a no-op in this SDK build, so we don't set it, and the bundled
> `sdk.json` has been aligned to the no-`_total` names. By contrast, counters sent over **OTLP**
> (business metrics) **do** get `_total` appended by the collector's Prometheus exporter — so business
> counters are queried as `orders_payments_captured_total`. Two pipelines, two conventions.

---

## Dashboards (bundled, provisioned)

| Dashboard | Source | Notes |
|---|---|---|
| **Temporal Server** | samples-server `temporal.json` | Server-side Go metrics — match the same Temporal server |
| **Temporal SDK** | samples-server `sdk.json` (patched) | `_total` suffixes stripped to match the Python SDK's pull names |
| **Ziggymart — Business & Custom Metrics** | `business.json` (hand-built) | OTLP push business metrics + custom workflow counters; guaranteed to match this app |

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
   - Filter by label: `{service_name="orders-worker-activity"}` or `{service_name="orders-worker-workflow"}`
   - `workflow.logger` and `activity.logger` output is forwarded via the OTel `LoggingHandler`

Correlate all three: copy a `trace_id` from a Loki log line and open it in Tempo.

> **Known limitation.** The `orders-service` (FastAPI) process forwards application logs to Loki, but
> uvicorn's own access/error loggers set `propagate=False`, so HTTP access lines do not appear in Loki.
> Worker logs (the primary target for this demo) flow fully. To capture uvicorn logs too, configure
> uvicorn's loggers to propagate or attach the OTel handler to them explicitly.
