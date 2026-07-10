# ADR-0026: Domain descriptors, scaffolding, and pluggable data converters

- **Status:** Accepted — Python foundation landed on branch `polyglot-domain-scaffolding`
  (PR #1); Java appkit + Java template follow in PR #2.
- **Date:** 2026-07-07
- **Related:** Extends ADR-0021 (protobuf IDL + activity version in command) and ADR-0022
  (domain core vs. application composition). Builds on ADR-0007 (shared namespace spec),
  ADR-0011 (local OCI chart delivery), and ADR-0004 (Worker Deployment / PINNED versioning).

## Context

The repo's first domain (`ziggymart` / `orders`) grew organically: task queues, worker profiles,
Helm charts, Grafana dashboards, and the Temporal data converter were wired by hand in multiple
places. Porting additional demos (customer PoCs, conference samples, polyglot stacks) repeated
that work and risked contract drift — especially the data converter (ADR-0022's cautionary
example: mismatched converters break payload deserialization across starters and workers).

We need a **repeatable, verifiable path** to land a new domain without copying the entire orders
tree, while keeping shared contracts (converter, namespace spec, task-queue constants) uniform.

## Decision

### 1. Domain descriptor (`config/domains/<domain>.yaml`)

Each domain gets a YAML descriptor that is the **within-domain wiring contract**:

- `domain` — key that MUST exist in `config/temporal/namespaces.yaml`
- `kernel` — optional; names the `libs/<kernel>/` package when it differs from the domain key
  (e.g. `ziggymart` → `orders`)
- `language` — `python` today; `java` in PR #2
- `data_converter` — symbolic ref resolved by appkit (default: pydantic/proto converter)
- `workers` — profile, kind, deployment name, task queue per worker split
- `workflows` — type, task queue, sample inputs (for future generic console triggers)
- `observability.dashboard` — whether a Grafana dashboard is provisioned

`compose/scripts/verify-domains.py` (wired into `just lint`) checks every descriptor against
the namespace spec and kernel `TaskQueue` constants. Drift fails CI offline.

### 2. Scaffolder (`compose/scripts/scaffold_domain.py`)

A Python script copies tokenized templates and patches repo integration points:

| Template | Output |
|---|---|
| `templates/domain/python/` | `libs/<domain>/`, `apps/temporal/workers/python/<domain>/` |
| `templates/charts/domain-workers/` | `deploy/charts/<domain>-workers/` |
| `templates/grafana/` | dashboard + provisioning under `compose/observability/grafana/` |

It also writes the descriptor, appends `namespaces.yaml`, stubs a Cloud overlay entry,
adds a chart-version TF variable, and patches `pyproject.toml` (workspace members, dependency
groups). **Manual follow-ups** remain documented: ArgoCD Application in `applications.tf`,
Grafana docker-compose mounts, `uv lock`, build/push/deploy.

`--root` and `--template-root` support offline pytest into a temp tree without polluting the repo.

Template defaults encode live-verified fixups: `VersioningBehavior.PINNED` on HelloWorkflow,
`startupProbe.enabled: false` for demo domains without orders-api, Grafana datasource uid
`prometheus-kind`.

### 3. Pluggable data converter (`appkit.domains`)

`libs/appkit/python/appkit/domains.py` loads descriptors and resolves `data_converter` to a
Temporal `DataConverter`. `appkit.temporal.connect()` accepts an optional converter; workers
and starters call `data_converter_for_namespace()` so every party in a domain shares the same
codec (ADR-0021 contract).

Today only `default` / `pydantic` / `json` resolve to `pydantic_data_converter`. Custom
converters register in `resolve_data_converter()` when needed.

### 4. What stays out of PR #1

- **Java appkit + `templates/domain/java/`** — PR #2 (Spring Boot composition kit)
- **Generic console trigger UI** — reads descriptor `workflows[].sample_inputs`; later milestone
- **In-image descriptor path** — fixed in PR #34 review: workers/starters read
  `TEMPORAL_DATA_CONVERTER` from settings (chart injects from descriptor at deploy time).
  `data_converter_for_namespace` remains for Phase B console (needs descriptor mount).
- **Scaffolder pyproject anchors** — string-replace patches fail loud if anchor drifts (F3).

## Consequences

**Positive**

- New Python domains scaffold in minutes; verifier catches queue/namespace drift before deploy
- Data converter is descriptor-driven, not re-decided per app
- Offline pytest (`compose/scripts/tests/test_scaffold_domain.py`) guards templates without
  committing a proof domain to git
- Human runbook at `docs/adapting-a-demo.md`

**Negative / trade-offs**

- Scaffolder patches are brittle (pyproject anchors, manual TF/compose steps)
- Descriptor is not yet baked into container images — namespace→converter lookup fails in-image
- Chart template copied from orders-workers carries orders-specific comments/keys demo domains
  do not use (harmless but noisy)

## Verification

- `just verify-domains` + `just lint` pass on `ziggymart` descriptor
- `pytest compose/scripts/tests/test_scaffold_domain.py` scaffolds + verifies offline
- Live hello proof (kind+OSS, pre-strip): HelloWorkflow COMPLETED; activity on
  `hello-activity-task-queue`; Grafana panels resolve with `prometheus-kind` uid
- Independent Temporal-aware `/code-review` required before merge (PR #1)

## Amendment (2026-07-10): Ruby, .NET, `runtime_version`, cross-SDK .NET payloads

Polyglot domain adoption now includes **Ruby** and **.NET** alongside Python, Java, Go, and
TypeScript. Templates live under `templates/domain/ruby/` and `templates/domain/dotnet/`;
`compose/scripts/build_domain_images.py` is the sole adapter for language-specific image build args.

### `runtime_version` (descriptor → Dockerfile ARG)

Optional per-worker `runtime_version` on `config/domains/*.yaml` pins the language runtime base
without forking Dockerfiles. Defaults match `config/dependencies.yaml` → `platform.runtimes` and
each `images/<language>.Dockerfile` ARG. Notable mapping: **dotnet** `runtime_version: net8.0` (or
`net10.0`) sets both `DOTNET_VERSION` (image tag `8.0`) and `TARGET_FRAMEWORK` (`net8.0`).

### Ruby runtime layout

Ruby workers use per-worker `Gemfile` + path gem to `libs/<domain>/ruby`. The image copies
`vendor/bundle` **and** mirrors `/libs` at runtime (Bundler resolves path deps even in deployment
mode) and sets `BUNDLE_DEPLOYMENT=1` / `BUNDLE_PATH=/app/vendor/bundle`. OSS/kind mTLS is wired in
the worker template via `Temporalio::Client::Connection::TLSOptions` reading the standard
`TEMPORAL_TLS_*` env vars injected by the chart.

### .NET cross-SDK payload interop

.NET's default `System.Text.Json` payload converter uses PascalCase property names and is
case-sensitive. Other SDK templates and the console `sample_inputs` catalog emit camelCase JSON
(e.g. `{"name":"Temporal"}`). Domain templates therefore ship `CamelCasePayloadConverter` and wire
`DataConverter.Default with { PayloadConverter = new CamelCasePayloadConverter() }` on worker
client connect — per Temporal .NET SDK guidance for multi-SDK interoperability.

### Console trigger on kind+OSS

Host-plane `/domain-trigger` requires `TEMPORAL_TRIGGER_TLS=true` when the backend is OSS (mTLS
certs mounted at `/etc/temporal/tls`). Workers in-cluster use the Worker Controller's injected
certs; the console trigger path is separate.
