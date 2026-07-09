# Adapting a demo domain

This runbook ports an external Temporal demo into this repo as a first-class **domain**.
A domain is one business use case (orders, a wealth tool, an AI-agent harness) that plugs into a production-shaped platform + observability plane.

The **domain descriptor** (`config/domains/<domain>.yaml`) is the single source of truth.
You author it; build, deploy, bootstrap, dashboards, and the console trigger catalog all reconcile to it.

See also: ADR-0026 (`docs/adr/0026-domain-descriptors-scaffolding-data-converter.md`), `docs/RUNMODES.md` for bring-up modes.

## The core contract: bring logic, inherit platform

| You **bring** (from the demo) | You **inherit** (from the platform, untouched) |
|---|---|
| Workflow + activity definitions | Temporal client + connection (mTLS, address, namespace) |
| Task-queue names (as code constants) | Data converter wiring (`data_converter` on the descriptor) |
| Which worker registers which workflow/activity | Structured logging, metrics, OTel export |
| The descriptor (worker topology + `sample_inputs`) | Worker tuning + autoscaling (WorkerAutoscaler) |
| External-dep decisions (mock-api vs real) | Grafana dashboard (scaffolded), console `/domain-trigger` UI |
| | Helm chart, ArgoCD Application, image build — all descriptor-driven |

If you edit platform code to adopt a demo, that is a platform bug — not a runbook step.

## Identity: one `domain` name, optional `namespace` override

There is **one** name per domain (`domain`), used everywhere.
There is **no** `kernel` field.

```yaml
domain: orders          # descriptor filename, console catalog key, libs/<domain>/ package
namespace: ziggymart    # OPTIONAL Temporal namespace; defaults to `domain` when omitted
```

| Identity | Meaning |
|---|---|
| `domain` | Descriptor filename, console catalog key, `libs/<domain>/`, worker path, image name (`<domain>-worker-<profile>`), digest key (`<domain>-<profile>`) |
| `namespace` | Temporal namespace only — the **only** identity that may differ from `domain` (e.g. keep `ziggymart` as the OSS/Cloud namespace while the domain key is `orders`) |

The flagship demonstrates the override: `config/domains/orders.yaml` has `domain: orders` and `namespace: ziggymart`.
The domain doctor checks that the resolved namespace exists in `config/temporal/namespaces.yaml`.

Worker code path (deterministic):

```text
apps/temporal/workers/<language>/<domain>/<profile>/
```

Read `config/domains/orders.yaml` for the full descriptor schema (comment header).

## Worker topology: descriptor owns the contract, code owns registrations

Each `workers[]` entry declares one worker profile — **language is per worker**, so one domain can be polyglot (Python workflow + Go activity + Java finalization, etc.).

```yaml
workers:
  - profile: workflow
    language: python
    kind: workflow
    task_queue: mydomain-workflow-task-queue
    deployment_name: mydomain-workflow-python
  - profile: activity
    language: go
    kind: activity
    task_queue: mydomain-activity-task-queue
    deployment_name: mydomain-activity-go
workflows:
  - type: HelloWorkflow
    task_queue: mydomain-workflow-task-queue
    sample_inputs:
      - label: happy_path
        input: {name: Temporal}
```

- **Config** owns which workers exist, their languages, kinds, and queues.
- **Code** owns which workflows/activities each worker's entrypoint registers, and activity routing to the activity queue (production split).
- `(language, profile)` **is** the pointer to the worker directory — no separate `entrypoint` field.

`verify-domain` asserts every derived worker dir exists, has an entrypoint, and every `task_queue` matches a `TaskQueue` constant in `libs/<domain>/`.

## Per-language dependencies

The platform does not impose one dependency model.
Each language uses its native toolchain; the scaffolder patches the manifest; the doctor verifies registration.

| Language | Dep manager | Scaffolder patches | Dockerfile | Doctor checks |
|---|---|---|---|---|
| **Python** | uv workspace + `[dependency-groups].<domain>-workers`, one `uv.lock` | `pyproject.toml` member, group, source, pyright roots | `images/python.Dockerfile` | Group + `libs/<domain>/python` in workspace members |
| **Java** | Gradle multi-project (`settings.gradle`) | `include` + `projectDir` per worker module | `images/java.Dockerfile` | Worker path in `settings.gradle` |
| **Go** | Per-worker `go.mod` (isolated module) | Worker dir with `go.mod` + `cmd/` | `images/go.Dockerfile` | `go.mod` in worker dir |
| **TypeScript** | pnpm workspace (`pnpm-workspace.yaml`, created on first TS scaffold) | Workspace package entries | `images/typescript.Dockerfile` | `package.json` in worker dir |

Build args are resolved in `compose/scripts/build_domain_images.py` (the only place language-specific build knowledge lives).
Optional per-worker `dockerfile:` on the descriptor overrides the default `images/<language>.Dockerfile`.
Optional per-worker `dependency_group:` (Python) defaults to `<domain>-workers` — use a separate group when the workflow worker must stay light and activity workers need heavier deps.

### Commit your lockfiles

Generated templates ship **without** resolved lockfiles.
After porting real dependencies, commit the lock artifacts so image builds are reproducible:

| Language | Action |
|---|---|
| **Python** | Run `uv lock` and commit `uv.lock` (and any `libs/<domain>/python/pyproject.toml` dep changes) |
| **Java** | Commit `gradle.lockfile` / version-catalog changes as you add deps (`just java-build` to verify) |
| **Go** | Run `go mod tidy` in each worker module and commit `go.sum` |
| **TypeScript** | Run `pnpm install` at the repo root and commit `pnpm-lock.yaml` |

`just adopt-domain` runs `uv lock` for Python before build; you still must commit the result.

## Build → deploy chain (descriptor-driven)

Nothing in this chain is hand-authored per domain anymore.

1. **BUILD** — `just build-images` iterates `config/domains/*.yaml` via `compose/scripts/build_domain_images.py`.
   Each worker becomes image `localhost:5001/<domain>-worker-<profile>` from its derived directory.
2. **DIGESTS** — `just worker-digests-json` emits a map keyed `<domain>-<profile>` for Terraform.
3. **CHART** — `just chart-publish` publishes `deploy/charts/<domain>-workers` (and embeds OSS bootstrap spec).
   Bump `Chart.yaml` `version` and the matching `<domain>_workers_chart_version` in `deploy/terraform/layers/cluster/variables.tf` on any template change (ADR-0011).
4. **DEPLOY** — `deploy/terraform/layers/cluster/applications.tf` `for_each` over descriptors creates one ArgoCD Application per domain; helm `workers[]` is derived from the descriptor.
5. **BOOTSTRAP** — `config/temporal/namespaces.yaml` drives OSS namespace creation (`render-oss-bootstrap.py`, temporal-server bootstrap Job, `just bootstrap-oss-namespaces`).
6. **OBSERVABILITY** — Scaffolded dashboards land under `compose/observability/grafana/dashboards/<domain>/`.
   `docker-compose.yml` glob-mounts `dashboards/` via `compose/observability/grafana/provisioning/dashboards/domains.yaml` — no per-domain compose edits.

Publish the chart **before** `terraform apply` — never chain `chart-publish && terraform apply` in one shell (ADR-0011).

## Domain doctor (`verify-domain` / `verify-domains`)

`compose/scripts/verify-domains.py` fails loud before build or deploy.

| Command | When |
|---|---|
| `just verify-domain <domain>` | Single domain — step 0 of `just adopt-domain` |
| `just verify-domains` | All domains — part of `just lint` |

**ERROR** (blocks deploy): identity resolves; worker dirs + entrypoints exist; language Dockerfile present; Python/Java/Go/TS manifest registration; queue integrity (constants match descriptor; workflow queues served; no orphans); chart dir exists and version ≥ cluster TF default; Grafana dashboard file when `observability.dashboard: true`.

**WARN** (proceeds): `workflows[]` entry without `sample_inputs` — won't appear in the console trigger catalog.

Each ERROR message states the exact fix.

---

## The journey (Steps 0–5)

### Step 0 — Read the demo (thinking, no repo changes)

Answer before touching the repo:

- Which **language per worker** (workflow worker vs activity workers may differ).
- Workflow and activity types, task queues, and worker boundaries.
- One or two **sample input** payloads for console trigger.
- External dependencies (mock-api vs real HTTP) — the main design choice you own.
- A short **domain key** (`[a-z][a-z0-9-]*`) — becomes the descriptor filename and code package name.

### Step 1 — Author the descriptor

```bash
just new-domain <domain>
```

Edits `config/domains/<domain>.yaml` — a commented starter with two Python workers and one `HelloWorkflow` sample.
Customize:

- `domain` and optional `namespace` (add a matching key to `config/temporal/namespaces.yaml` if you override).
- `k8s_namespace` (default scaffold uses `orders` to share the cluster mTLS secret).
- `workers[]` — set `language`, `kind`, `profile`, `task_queue`, `deployment_name` per worker.
- `workflows[]` + `sample_inputs` — drives the console trigger catalog.
- `data_converter` (default `default`), `autoscaling`, `observability.dashboard`.

For Cloud namespaces, ensure `deploy/terraform/layers/cloud/terraform.tfvars` has an overlay entry (the scaffolder appends a stub when that file exists).

### Step 2 — Generate (idempotent)

```bash
just scaffold-domain <domain>
```

Reads the descriptor (no `LANG` flag) and generates **only missing** artifacts:

| Output | Path |
|---|---|
| Domain library skeleton | `libs/<domain>/` (per language used) |
| Worker apps | `apps/temporal/workers/<language>/<domain>/<profile>/` |
| Helm chart | `deploy/charts/<domain>-workers/` |
| Grafana dashboard | `compose/observability/grafana/dashboards/<domain>/<domain>.json` |
| Namespace entry | appends to `config/temporal/namespaces.yaml` |
| TF chart version var | `deploy/terraform/layers/cluster/variables.tf` |
| Language manifests | `pyproject.toml`, `settings.gradle`, `go.work`, or `pnpm-workspace.yaml` as needed |

Re-run after descriptor edits — **zero diff** when nothing changed (idempotent).

Offline guard: `uv run pytest compose/scripts/tests/test_scaffold_domain.py`.

### Step 3 — Port the business logic (the real work)

Replace Hello stubs in `libs/<domain>/` with your demo's workflows and activities.
Per worker entrypoint, register the right workflows/activities and keep **production split** routing (activities on the activity queue, not the workflow queue).

#### Python

- Domain library: `libs/<domain>/python/<domain>/`
- Workers: `apps/temporal/workers/python/<domain>/<profile>/main.py`
- Queue constants: `libs/<domain>/python/<domain>/shared/temporal_ids.py` — every descriptor `task_queue` must match.
- Route activities via the template `run_activity` helper (`task_queue=TaskQueue.ACTIVITY`).
- Set `VersioningBehavior.PINNED` on versioned workflow classes (ADR-0004).

#### Java

- Domain library: `libs/<domain>/java/`
- Workers: `apps/temporal/workers/java/<domain>/<profile>/`
- Queue constants: `shared/TemporalIds.java` (doctor scans `*Ids.java`).
- Route with `ActivityOptions.setTaskQueue(...)` on stubs; `@WorkflowVersioningBehavior(PINNED)` on impls.
- Java charts omit container `command` — image `ENTRYPOINT` runs the boot jar.

#### Go

- Worker module: `apps/temporal/workers/go/<domain>/<profile>/` with `go.mod` and `cmd/main.go`
- Queue constants: co-located `internal/temporalids/` (must match descriptor).
- Register activities with explicit activity names matching Python/Java `ActivityName` constants.
- Enable Worker Versioning via `BuildID` + `UseBuildIDForVersioning` + `DeploymentOptions` (PINNED default).

#### TypeScript

- Shared ids: `libs/<domain>/typescript/src/temporal-ids.ts`
- Workers: `apps/temporal/workers/typescript/<domain>/<profile>/`
- Use `proxyActivities` with `taskQueue: TaskQueue.ACTIVITY` in workflow code.

Gate before deploy:

```bash
just verify-domain <domain>
```

### Step 4 — Bring up (kind + OSS recommended first)

**Host plane first** — console must be up before kind mutations (`just preflight` enforces this).

```bash
just host-up oss                # OSS host profile (console, Grafana, mock-api)
# or: just host-up              # Cloud profile
```

Ensure the kind substrate exists (`just kind-up`) or the full cluster (`just cluster-up oss`
/ `just platform-up oss` on a fresh machine).

Adopt the domain end-to-end:

```bash
just adopt-domain <domain>
```

Runs, in order: `verify-domain` → `uv lock` → `build-images` → `push-images` → `chart-publish` → `terraform apply` (preserves current `temporal_backend` and existing worker digests) → `bootstrap-oss-namespaces`.

`terraform apply` is the single cluster-mutating step.
Prefer `just adopt-domain` over full `just cluster-up` when adding one domain alongside orders (see `docs/RUNMODES.md` surgical redeploy).

**Orders-only extras** (not part of the workers `for_each`): `orders-api`, `orders-data` — legitimate additive richness for the flagship demo.

### Step 5 — Verify (minimum footprint)

1. **Console trigger** — http://localhost:8086 → **Trigger domain workflow** (`/domain-trigger`).
   Your domain appears in the catalog (orders is excluded by default via `GENERIC_TRIGGER_EXCLUDE_DOMAINS`).
   Pick workflow + sample → trigger **exactly one** execution → confirm **COMPLETED**.
2. **Queue routing** — activity tasks land on the activity worker's queue, not the workflow queue.
   ```bash
   just k exec -n temporal deploy/temporal-admintools -- \
     temporal task-queue describe --address temporal-internal-frontend:7236 \
     --namespace <namespace> --task-queue <domain>-activity-task-queue
   ```
3. **Grafana** — dashboard under folder `<domain>`; datasource uid `prometheus-kind`; `namespace="<namespace>"` in PromQL.
   Schedule-to-start panels show NaN when idle — expected with no backlog.
4. **Terminate** the test execution when done.

**OSS console trigger note:** the Temporal frontend is ClusterIP-only from Docker.
If trigger fails with TLS handshake errors, port-forward and recreate the console:

```bash
kubectl port-forward -n temporal svc/temporal-frontend 17233:7233
# set TEMPORAL_TRIGGER_ADDRESS=host.docker.internal:17233 in config/local-oss-kind.env
# mount mTLS client certs - see docs/adapting-a-demo.md / extract from temporal-client-mtls secret
docker compose up -d --no-deps --force-recreate platform-console
```

---

## Quick reference

| Task | Command |
|---|---|
| Starter descriptor | `just new-domain <domain>` |
| Generate / reconcile | `just scaffold-domain <domain>` |
| Doctor (one) | `just verify-domain <domain>` |
| Doctor (all) | `just verify-domains` |
| Adopt end-to-end | `just adopt-domain <domain>` |
| Build all worker images | `just build-images` |
| Worker digests JSON | `just worker-digests-json` |
| Publish charts | `just chart-publish` |
| OSS namespace bootstrap | `just bootstrap-oss-namespaces` |
| Console gate | `just preflight` |

## Template locations

| Language | Template root |
|---|---|
| Python | `templates/domain/python/` |
| Java | `templates/domain/java/` |
| Go | `templates/domain/go/` |
| TypeScript | `templates/domain/typescript/` |
| Helm chart | `templates/charts/domain-workers/` |
| Grafana | `templates/grafana/dashboard.json` |

## Related platform docs

- `docs/RUNMODES.md` — host plane, kind, OSS vs Cloud, surgical redeploy
- `docs/adr/0026-domain-descriptors-scaffolding-data-converter.md` — design record (some manual steps there are superseded by this runbook)
- `docs/DEMO_SCRIPT.md` — flagship orders retail demo (separate from generic domain adoption)
