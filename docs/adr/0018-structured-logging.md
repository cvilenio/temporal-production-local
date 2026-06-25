# ADR-0018: Polyglot structured logging — one schema, replay-safe, agent-collected

- **Status:** Accepted
- **Date:** 2026-06-25
- **Related:** ADR-0001 (polyglot shared-kernel layout — `obslog` is a sibling kernel),
  ADR-0006 (codec server — the DataConverter/PayloadCodec analogy this borrows), ADR-0014
  (local visibility plane — host-side observability), ADR-0015 (substrate-aware console).
  First pillar of the observability track; metrics (Prometheus/OTel, Temporal Cloud
  OpenMetrics) follow in a later ADR.

## Context

Logging was not standardized and only half-wired. `init_observability()` attached **only** an
OTel `LoggingHandler` to the root logger — logs pushed to Loki via OTLP but **nothing went to
stdout**, so business logs were invisible in `kubectl logs` / Headlamp / Docker Desktop pod
views. There was no shared facade: each module called `logging.getLogger` ad hoc, the workflow
hand-rolled a `_log_ctx()`, `mock-api` used `print()` (Docker-only, never reaching Grafana),
and the console used f-string logs. No language-neutral schema, no concurrency-safe context
propagation, no type-robust serialization.

The repo is Python-only today but **polyglot by shape** (`libs/<domain>/python/`). Logging has
to be designed so a future Go/TypeScript worker emits the *same* records, the way Temporal's
own polyglot support rests on a serialization contract.

## Decision

### 1. A shared logging kernel: `obslog` (`libs/logging/python/`)

A dedicated workspace package — a sibling to the orders kernel (ADR-0001) — so services that do
**not** depend on the orders kernel (`console`, `mock-api`, `codec-server`) still share one
facade. A future `libs/logging/go/` mirrors the schema. `obslog` depends only on `structlog`;
OpenTelemetry is imported lazily so a stdout-only consumer needs no OTel.

### 2. `structlog`, because its pipeline IS the codec model

`structlog`'s **processor pipeline** is structurally the same as Temporal's
`DataConverter`/`PayloadCodec`: an ordered chain of transforms ending in a wire format. So the
type-robustness requirement is just a processor — `obslog.serialize.safe_serialize`, a "log
codec" that coerces any value to a JSON-safe form (pydantic→`model_dump`, dataclass→`asdict`,
`Decimal`/`datetime`/`UUID`→string, `bytes`→base64, set/tuple→list, exception→`{type,message}`)
with a worst-case `repr()` fallback that **never raises**. Deliberate leniency: accept more
types at the interface, enrich, and degrade gracefully rather than refuse to serialize. The
language-neutral wire schema is the **OTel logs data model**; `structlog` is just the Python
emitter onto it.

### 3. The schema (language-neutral)

| Layer | Fields |
|---|---|
| Resource | `service.name`, `service.namespace` (= domain, e.g. `ziggymart`), `service.instance.id`, `service.version` (worker Build ID when present) |
| Core | `timestamp` (ISO-8601 UTC), `level`, `logger`, `event` (message) |
| Context | Temporal: `workflow_id`, `run_id`, `workflow_type`, `activity_id`, `activity_type`, `attempt`, `task_queue`. Business: `order_id`, `trace_id`, `step` |

`trace_id` is the correlation key across logs ↔ traces ↔ search attributes. One stdlib
`ProcessorFormatter` renders **both** `structlog`-native and foreign records — so
`workflow.logger`, `activity.logger`, uvicorn, and sqlalchemy all land in the same schema.

### 4. Replay-safety boundary

| Context | Logger | Enrichment |
|---|---|---|
| Workflow (`@workflow.defn`) | `workflow.logger` (kept) | `extra=wf_log_extra(...)` built from deterministic workflow state |
| Activity (`@activity.defn`) | `activity.logger` (kept) | `with obslog.bound(...)` |
| Plain async (api, mock-api, console, db) | `obslog.get_logger()` | FastAPI middleware binds `request_id`/route via `bound()` |

`workflow.logger` suppresses duplicate lines on replay and injects workflow id/run id/type.
Inside a workflow we **never** use `obslog.bound()` (contextvars): contextvar state across the
deterministic sandbox / replay is a footgun, so workflow context is bound explicitly from
deterministic state via `wf_log_extra()`. Outside the sandbox, `bound()` wraps
`structlog.contextvars` — concurrency-safe (per-coroutine/thread), so two orders processed at
once never cross-contaminate; nested client/db logs inherit the bound business context for free.

### 5. Two sinks, and a k8s-faithful collection topology

Every service emits **JSON to stdout** (the k8s / 12-factor contract) — fixing the Headlamp /
Docker Desktop gap. From there:

- **Kubernetes (kind):** a **Grafana Alloy DaemonSet** (`deploy/charts/alloy`) tails
  `/var/log/pods`, enriches each line with `k8s.namespace_name/pod_name/container_name/
  node_name` (`discovery.kubernetes` + relabel), and ships to the host backend. Apps log
  stdout-only (`LOG_OTLP_PUSH=false`) — no direct app→backend push. This is the real pattern
  (app→stdout→node agent→backend), the same shape as a GKE fluentbit agent → Cloud Logging.
  The **agent holds the backend endpoint; the app never does**.
- **Host plane (Docker Compose):** services with no node agent (`mock-api`) push OTLP straight
  to lgtm on the compose network. `console`/`codec-server` are stdout-only (Docker Desktop).

### 6. Observability is a separate, durable tier — not in the workload cluster

`orders-db` is **workload state** (ephemeral, dies with kind, like a customer's app DB). The
observability backend is **operational state about workloads** — deliberately a separate,
more-durable tier that outlives any single workload cluster, so SLO history and postmortems
survive a rebuild. Enterprises run it as Grafana Cloud / Datadog / a central obs cluster, not
inside each ephemeral cluster — and that is the only topology where the later "scrape Temporal
Cloud's OpenMetrics endpoint" integration is natural (an external backend pulling from
`metrics.temporal.io`). So host-LGTM stays host-side, reframed as that central tier (durable via
the `lgtm-data` volume). The host publishes Loki/OTLP ingest ports so the in-cluster Alloy
agent can ship *out* to it (`docker-compose.yml`); the old inbound `host:4318→kind` mapping was
removed (`deploy/terraform/kind-config.yaml`) — the flow is agent-out now, not inbound.

## Consequences

- Business logs are finally visible in Headlamp / Docker Desktop (stdout JSON) **and** Grafana
  Loki (Alloy-collected), correlatable to traces via `trace_id`.
- One facade and schema across all Python services; `print()` and ad-hoc loggers retired.
- New surface to maintain: the `obslog` kernel and the Alloy chart. The Alloy image is pulled
  like the other third-party images; its chart is local (published by `just chart-publish`).
- **Polyglot extension path — share the contract, not the code.** The shared core is a single
  language-neutral artifact, `libs/logging/schema/log-schema.json` (well-known keys + types +
  required set; per-call fields free). It is the same pattern as OTel semantic conventions /
  Temporal's converters: one spec, a thin idiomatic emitter per language. Python's emitter
  (`obslog`) references the keys via `obslog.schema` and a conformance test
  (`libs/logging/python/tests/test_schema_conformance.py`) validates its output against the
  contract. A future `libs/logging/go` (slog/zap) or `libs/logging/ts` (pino) implements the
  same shape and ships its own conformance test against the SAME json — no shared implementation
  to maintain across languages. The Alloy collection layer is already language-agnostic (it
  collects any pod's stdout).
- **Deferred (next ADR):** metrics. The collection tier will scrape Temporal Cloud's
  OpenMetrics endpoint (`metrics.temporal.io`, Bearer API key held by the collector) and the
  SDK/server Prometheus endpoints — same "external durable tier reaches in" topology.
