# 0016 — Polyglot structured logging (obslog + Alloy agent collection)

- **Status:** **LANDED + LIVE-VALIDATED ON KIND+CLOUD (2026-06-25).** Committed to `main`
  (`671a29e`) and pushed. End-to-end order ran to **completed** on `ziggymart` with logs flowing
  to stdout + Loki.
- **Date:** 2026-06-25
- **ADRs:** **ADR-0018** (new — structured logging). First pillar of the observability track;
  metrics (Prometheus/OTel + Temporal Cloud OpenMetrics) is the next ADR.

## Why

Logging was unstandardized and half-wired: `init_observability()` attached **only** an OTel
`LoggingHandler` to the root logger, so logs went to Loki via OTLP but **never to stdout** —
business logs were invisible in `kubectl logs` / Headlamp / Docker Desktop. No shared facade
(ad-hoc `getLogger`, a hand-rolled workflow `_log_ctx`, `mock-api` on `print()`), no
language-neutral schema, no concurrency-safe context, no type-robust serialization. The repo is
Python-only today but polyglot by shape, so the schema had to be designed for future
Go/TS workers. Collection was also unfaithful — apps pushed OTLP directly instead of the real k8s
pattern (a node agent tailing pod stdout).

## Done this session (code + docs)

- **`obslog` kernel** (`libs/logging/python/`): new uv workspace member, sibling to the orders
  kernel (ADR-0001) so `console`/`mock-api`/`codec-server` (no orders dep) share one facade.
  `init_logging` (stdout JSON always + optional OTLP push), `get_logger`, `bound()` (contextvars),
  `wf_log_extra()`. `serialize.py` = the "log codec" (ordered coercion → `repr` fallback, never
  raises). structlog-based; OTel imported lazily so a stdout-only consumer needs no OTel.
- **Shared contract**: `libs/logging/schema/log-schema.json` (the language-neutral core) +
  `obslog/schema.py` constants + a conformance test (`tests/test_schema_conformance.py`) — first
  real tests in the repo (3, passing). "Share the contract, not the code."
- **Replay boundary**: workflows keep `workflow.logger` + `wf_log_extra` (deterministic, no
  contextvars in the sandbox); activities/plain-async use `activity.logger`/`get_logger` + the
  concurrency-safe `bound()`. Quieted the SDK's message-appended workflow/activity info
  (`*_info_on_message=False`, kept `*_info_on_extra=True`) so `event` is clean.
- **Wiring**: `telemetry.py` delegates the root pipeline to obslog; `config.py` adds
  `LOG_LEVEL/LOG_FORMAT/LOG_OTLP_PUSH` + service identity; workflows/activities/api/worker/
  mock-api/console/codec-server all on obslog (`print()` retired).
- **Alloy DaemonSet** (`deploy/charts/alloy`): tails `/var/log/pods`, enriches with k8s metadata
  (`k8s_namespace_name/pod_name/container_name/node_name`), parses our JSON, ships to host Loki.
  Seeded as an ArgoCD Application at **sync-wave -1** (`applications.tf`), published by
  `just chart-publish`, version in `variables.tf`.
- **Infra topology**: host LGTM reframed as the durable, separate observability tier (Cloud
  Logging/GMP analog). `docker-compose.yml` publishes Loki/OTLP host ports (3100/4317/4318) so the
  in-cluster agent ships **out**; the dead inbound `host:4318→kind` mapping removed
  (`kind-config.yaml`). App→backend log push is off on kind (`LOG_OTLP_PUSH=false`); host-plane
  mock-api pushes OTLP directly.
- **OTLP endpoint fix**: pointed worker/orders-api OTLP at `host.docker.internal:4317` (gRPC) so
  traces/business-metrics export succeeds instead of spamming `localhost:4317 UNAVAILABLE`.
  (orders-api was wrongly on `4318`/HTTP.)
- **Sunset right-size** (`orders-workers` values): `scaledownDelay 30m→10m`, `deleteDelay 2h→30m`
  — matched to this app's actual workflow length so drained versions clean up promptly without
  orphaning PINNED in-flight orders.
- **Docs**: ADR-0018 (new), OBSERVABILITY.md logging section, `lint-manifests.sh` now covers alloy.

## Verification

- **Static (DONE):** `poe lint` (ruff+pyright) green; `poe test` 3/3 (schema conformance, incl. the
  foreign `workflow.logger`/`activity.logger` `extra=` path); `helm lint` + `kubeconform` +
  sync-wave green on all charts. obslog smoke: pydantic/Decimal/bytes/raw `object()` all coerce,
  never raise; structured tracebacks with `show_locals=False`.
- **Live (DONE):** clean teardown → `up-cloud-kind` → `headlamp-reload` → `platform-up` (kind
  recreated from new config, images rebuilt with obslog, alloy seeded). Happy-path order
  `ORD-RT0EFK6RJ5NND5NH` → **completed**, **0 non-determinism**. Confirmed: business JSON in
  worker/api **pod stdout** (the Headlamp gap, now closed); activity nested **httpx logs inherit
  `bound()` context** (concurrency-safe); Alloy 3/3 Running, shipping with `k8s_*` labels in Loki;
  mock-api in Loki via OTLP (gap 4 closed); `trace_id` consistent api→workflow→activity→DB.
  After the clean-`event` + OTLP-endpoint redeploy, `event` reads clean and new pods emit **0**
  export errors.

## Gotchas / observations

- **Worker-version sunset = lingering old pods.** After a redeploy, the previous Build ID's pods
  stay up for `scaledownDelay` (was 30m) to drain PINNED in-flight orders. They carried the old
  (unset) OTEL endpoint and kept emitting `localhost:4317` errors until scaled down. Hard floor:
  `scaledownDelay` MUST exceed max workflow execution time or a pinned in-flight order loses its
  only poller and stalls. Right-sized to 10m this session. To force-drain early:
  `kubectl -n orders scale deploy <old-version-deploy> --replicas=0` (controller does not revert).
- **Loki "blank message" lines are foreign log sources, not ours.** `| json | line_format
  "{{.event}}"` blanks out uvicorn access logs (non-JSON, `propagate=False`) and Postgres/CNPG
  logs (JSON but `msg`/`ts`, not `event`). Scope queries by container, e.g.
  `{k8s_namespace_name="orders", k8s_container_name=~"worker|orders-api"} | json | line_format
  "{{.event}}"`, or fall back `{{ if .event }}…{{ else if .msg }}…{{ else }}{{ __line__ }}{{ end }}`.
- **Cosmetic:** disabling `*_info_on_message` removed the dict appended to the workflow/activity
  message string; the structured `temporal_workflow`/`temporal_activity` fields remain.
- The sunset change is in chart `orders-workers 0.1.6` — takes effect on the **next**
  `platform-up`, not the currently-running pods.

## Next / follow-ups

- **Metrics pillar (next ADR):** wire the Prometheus pull on kind (pod-scrape) and scrape Temporal
  Cloud's OpenMetrics endpoint (`metrics.temporal.io`, Bearer key in the collection tier) — same
  "external durable tier reaches in" topology this session established.
- **uvicorn access logs**: set them to propagate (or attach the obslog formatter) so HTTP access
  lines render in the shared schema instead of plain text.
- **Polyglot proof**: when a Go/TS worker lands, add `libs/logging/<lang>/` emitting the same
  `log-schema.json` shape + its own conformance test; Alloy collection is already language-agnostic.
- Optional: console/codec-server are stdout-only by design (no OTel dep) — revisit if their logs
  are wanted in Grafana.

## Follow-up spike (next session): ClickHouse/ClickStack vs Loki, side-by-side

**Why.** Grafana Explore's structured-log ergonomics are weak. Grafana **Logs Drilldown**
(`grafana-lokiexplore-app`, already installed on the bundled Grafana 13.0.1 —
`http://localhost:3000/a/grafana-lokiexplore-app`) is the in-stack upgrade and should be tried
FIRST. If it's still not enough, evaluate **ClickStack** (ClickHouse + OTel Collector + HyperDX,
all OSS) for its log-explorer UX. The OTel-logs-model design means this is a *config* swap, not a
code one — so a dual-ship side-by-side is cheap and low-risk.

**Key principle — app side is untouched.** `obslog`, the schema (`libs/logging/schema/
log-schema.json`), and stdout JSON do NOT change for any backend. The swap lives in
collection-exporter + backend + UI. Keep the committed Loki path intact; ClickStack runs
*alongside* (dual-ship) for the bake-off.

**Spike plan (branch, additive, opt-in):**
1. **Compose overlay** `compose/clickstack.yml` (don't touch `docker-compose.yml` base): add
   `clickhouse` (server + named volume + TTL), an OTel Collector with the contrib
   `clickhouseexporter`, and `hyperdx` (UI). Pick non-conflicting host ports (HyperDX UI e.g.
   `:8090`-ish — check the console/viz-proxy map first; ClickHouse `8123`/`9000`; a 2nd OTLP
   listener if you don't reuse lgtm's collector). Bring up with
   `docker compose -f docker-compose.yml -f compose/clickstack.yml up`.
2. **Dual-ship from Alloy** (`deploy/charts/alloy/templates/configmap.yaml`): keep the existing
   `loki.write "backend"`, ADD a parallel branch `loki.source.file → loki.process →
   otelcol.receiver.loki → otelcol.exporter.otlphttp` pointed at the ClickStack collector
   (`host.docker.internal:<otlp>`). `loki.source.file.forward_to` takes a LIST — fan out to both
   the Loki processor and the otel bridge. Gate it behind a values flag (`clickstack.enabled`,
   default false) + a new chart version so the committed path is unaffected.
   - Host-plane (`mock-api`): simplest is to leave it on lgtm, or add a 2nd `OTEL_EXPORTER`
     target; logs from kind workers are enough for the UI bake-off.
3. **Same data, both UIs:** run the usual happy-path + a flaky/ghost order, then compare on
   identical logs:
   - Loki: Grafana **Logs Drilldown** (`{k8s_namespace_name="orders"}`)
   - ClickHouse: **HyperDX** search/explorer
   Score: field faceting, pattern clustering, collapse/expand, trace correlation, high-cardinality
   filter on `order_id`/`trace_id`, time-to-answer for "why did order X fail".
4. **Optional Grafana-only variant** (no new UI): add the official `grafana-clickhouse-datasource`
   and query ClickHouse via SQL — keeps Grafana but loses Drilldown; useful only as a stepping
   stone, not the goal.

**Files in play:** `compose/clickstack.yml` (new), `deploy/charts/alloy/{values.yaml,
templates/configmap.yaml}` (dual-ship flag + bump version), maybe `apps/platform/console`
(a HyperDX embed tab, ADR-0014/15 pattern) — NOT `libs/logging/*` or any app code.

**Decision gate → ADR-0019** if adopting: cut Alloy/host exporters to ClickHouse, replace the
Loki datasource/Drilldown with HyperDX (or the ClickHouse datasource), fold traces+metrics into
the same ClickHouse later and retire `lgtm`. Capture the tradeoff: ClickHouse is a real columnar
DB to run/size/retain (TTL, partitions, merges) vs Loki's cheap object-store model — heavier
footprint, much stronger query/UX, high-cardinality-friendly. For the disposable kind workbench
that's a bigger container; for the "central durable observability tier" framing it's a legit
enterprise choice.

**Caveats to remember:** our dotted OTel attrs (`service.name`, `k8s.*`) fit ClickHouse columns/
`Map` better than Loki labels (which forced `_`); `order_id`/`trace_id` are cheap columns in CH
(Loki hates them as labels); and dual-ship is the safe eval mode — never rip out Loki until the
ADR lands.
