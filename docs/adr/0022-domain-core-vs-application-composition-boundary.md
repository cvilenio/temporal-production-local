# ADR-0022: Domain core vs. application composition — the /libs vs /apps boundary

- **Status:** Accepted — **migration executed** (libs/appkit created; composition moved into
  the apps; `libs/orders` is domain-core only). Done as two PRs (3a extraction, then app
  composition roots).
- **Date:** 2026-06-26 (migration executed 2026-06-27)
- **Related:** Refines ADR-0001 (shared-kernel monorepo layout). Builds on ADR-0005 (connection
  profiles → settings field-groups), ADR-0004 (Worker Deployment identity = a contract), and
  ADR-0021 (the data converter is a cross-app contract — the worked cautionary example here).

## Context

ADR-0001 chose a **fat shared kernel + razor-thin apps**: `apps/*/main.py` is a one-liner and
*all* composition (env-driven `Settings`, the DI container, worker profiles, `run_worker`, the
FastAPI factory/lifespan) lives in `libs/orders`. In practice this blurred "lib = reusable domain"
— the kernel reads `os.getenv`, builds clients, and owns process lifecycle, none of which is
domain logic.

Separating the concerns cleanly, the codebase has **three classes** of code, not two:

1. **Domain core** — app-agnostic engine + the utilities it orchestrates (workflows, activities,
   the proto contracts, the client *ports*, db models, shared ids/errors).
2. **App definition / assembly** — the deployable: entrypoint, lifecycle, and *this app's* concrete
   choices (which ports it wires, with what lifetimes, which env it needs).
3. **Reusable app-definition blocks** — the composition building blocks that repeat across apps.

Class 3 itself splits in two:

- **3a — generic kit (domain- *and* app-agnostic).** There is no "orders Temporal client" — there
  is *a* Temporal client factory, *a* SQL engine factory, *a* `run_worker(profile)` loop, *a*
  telemetry bootstrap, *reusable* settings field-groups. Nothing here names a workflow, activity,
  or external service of any domain.
- **3b — domain composition (domain-aware, app-agnostic).** "Which ports orders wires, from what
  config, registering which code": the bespoke `OrdersServiceClient`/`MockApiClient` providers, the
  orders settings delta, the worker-profile registry, orders-specific Temporal usage.

The litmus test that separates 3a from 3b: **does the block name a workflow, activity, or external
service of the domain?** No → 3a. Yes → 3b.

## Decision

**The invariant is reusability, not domain-ness:**

- **`/libs` = reusable, importable, *not deployable*.** Two species, named so discovery is obvious:
  - domain kernels — `libs/orders`, `libs/logging` (class 1).
  - the generic composition kit — **`libs/appkit`** (class 3a). Domain- and app-agnostic.
- **`/apps` = the deployable assembly** (class 2) — each app is a one-stop shop for its entrypoint,
  lifecycle, and its own wiring.

**3b lives per-app, not in a shared module.** The provider *type* (`Singleton` vs `Resource` vs
`Factory`) is a **lifecycle policy**, and lifecycle is a property of the *process*, not the domain.
The same `OrdersServiceClient` class is correctly a `Factory` in a one-shot CLI, a `Singleton` in a
long-running worker, and a `Resource` (open/close a pooled client) in a request-scoped service. A
shared module that pre-declares `orders_service = Singleton` leaks a runtime policy into a layer
that can't know the runtime. So each app's composition root wires the one or two ports it actually
uses. The repetition is a few lines and is *desirable* — it forces each app to reason about what it
truly needs.

### The rule that keeps this safe: policy vs. contract

Per-app freedom applies to **policy**. Some things *look* like wiring but are **contracts** that
must be identical across every app or the system breaks — those stay shared and apps *consume* them,
never redefine them.

| Kind | Who decides | Examples |
|---|---|---|
| **Policy** (per-app, free) | the app's composition root | provider lifetime (Singleton/Resource/Factory); max-concurrency; pool sizes; which ports this app builds |
| **Contract** (shared, uniform) | kit / domain — apps consume | the **Temporal data converter** (ADR-0021); Worker-Deployment identity + versioning behaviour (ADR-0004); task-queue / namespace / search-attribute keys |

> **Worked cautionary example — the data converter.** `pydantic_data_converter` (+ the proto
> encoders) is passed at `Client(...)` construction, so it *looks* like per-app wiring. It is not.
> If the api wired one converter and the workers another, proto payloads would stop deserializing.
> It is a contract: the `appkit` Temporal-client builder bakes it in, and every app gets the same
> one. Never re-decide it per app.

### Guardrails that make per-app wiring maintainable

1. **Domain ports stay lifecycle-agnostic.** No module-level globals/singletons; constructors take
   their deps; a port that owns a resource (pooled `httpx`) exposes explicit `open()/aclose()` so an
   app can wrap it as `Resource` *or* `Singleton`. The class never chooses its own lifetime — that's
   what earns the app the freedom to.
2. **The kit owns the error-prone *construction*; the app owns the *lifecycle*.** `appkit` exposes
   `build_temporal_client(profile)` that gets tls / api-key / mTLS / interceptors / converter right
   once; each app calls that one factory and only chooses the provider type around it. Repetition
   shrinks to the trivial, genuinely-per-app bit; construction stays DRY and correct.
3. **Settings are shared field-*groups*, composed per app.** `appkit` provides mixins
   (connection-profile, worker-tuning, telemetry); each app composes only the fields it uses, so an
   app that never calls mock-api doesn't carry `mock_api_url`.

### Litmus tests (for reviews)

- A file in **domain core** does *none* of: read env, build a client/DB connection, own a process
  lifecycle. (`grep -rn "os.getenv\|os.environ" libs/<domain>` should return nothing.)
- A block is **3a** if it names no workflow/activity/external-service; otherwise it's **3b** → an app.
- If two apps must agree on a value for the system to work, it's a **contract** → kit/domain, not
  per-app wiring.

## Consequences

- **Gain:** `/libs` becomes honestly reusable (domain + a generic kit); `/apps` reads as the place
  to see how an app boots; lifecycle decisions live where the runtime is known; the dangerous
  shared invariants (converter, versioning identity) are named and centralized.
- **Cost:** a few lines of provider wiring repeat across the app composition roots (mitigated by the
  kit owning construction). One new lib (`appkit`) and a sizable, mechanical migration.
- **Polyglot:** `appkit` is inherently per-language (`libs/appkit/python`); a future Go/TS app gets
  its own `appkit` in that language. The *contracts* (proto, queue/ns/SA names) remain
  language-neutral and shared (ADR-0021).

## Migration plan

Mechanical and stageable; each phase keeps the gate green. **Executed** (2026-06-27) as two PRs —
phase A (3a extraction into `libs/appkit`) then phase B (app composition roots + deletions) —
separate from the proto work. File-by-file disposition of `libs/orders/python/orders/`:

| Today | Class | Destination |
|---|---|---|
| `workflows/`, `activities/` (incl. `contract_gate.py`) | domain | stays `libs/orders` |
| `clients/mock_api.py`, `clients/orders_service.py` | domain port | stays `libs/orders` — make lifecycle-agnostic (explicit `aclose()` if pooled) |
| `shared/contracts.py`, `_pb/`, `shared/models.py`, `shared/errors.py`, `shared/ids.py`, `shared/contract_version.py` | domain / contract | stays `libs/orders` |
| `shared/temporal_ids.py` (task queues, SA keys) | **contract** | stays `libs/orders` (shared, consumed not redefined) |
| `db/models.py` | domain | stays `libs/orders` |
| `db/engine.py` (build async engine from DSN) | **3a** | `libs/appkit` (generic SQL engine factory) |
| `services/temporal.py` → `connect()` / client build | **3a** | `libs/appkit` `build_temporal_client(profile)` — **bakes in the data converter contract** |
| `services/temporal.py` → `start_order_workflow`/`cancel_order`/`reset_workflows` | domain | stays `libs/orders` (a domain service over the client) |
| `config.py` generic fields (connection-profile, worker-tuning, telemetry) | **3a** | `libs/appkit` settings mixins/field-groups |
| `config.py` orders deltas (`mock_api_url`, `orders_service_url`, `demo_reset_enabled`) | **3b** | per-app settings (compose the mixins + the delta) |
| `worker.py` → `run_worker` loop, `_deployment_config`, telemetry bootstrap | **3a** | `libs/appkit` (generic run-a-worker-from-profile + deployment-config-from-env builder) |
| `worker.py` → `WORKER_PROFILES`, `_build_activity_group` | **3b** | per-app: each worker app declares its own `WorkerProfile` inline |
| `containers.py`, `resources.py` (DI providers) | **3b** | dissolve into each app's composition root (choose provider lifetimes there) |
| `api.py` (FastAPI factory, lifespan, routes) | class 2 | move into the deployable `apps/business/orders-api/python/` (the app skeleton/lifespan pattern may be a thin `appkit` helper; routes are the app) |

Phases:
1. Create `libs/appkit/python` skeleton (workspace member).
2. Extract 3a into `appkit`: `build_temporal_client` (with converter), SQL engine factory, telemetry
   bootstrap, settings field-groups, `run_worker`, deployment-config builder, optional FastAPI skeleton.
3. Move the orders-api app (routes + lifespan) into `apps/business/orders-api/python/`.
4. Give each app a composition root: wire its ports from `appkit` + `libs/orders` ports + its own
   settings, choosing provider lifetimes locally. Delete `containers.py`/`resources.py`.
5. Push `WORKER_PROFILES` per-app; remove the composition remnants from `libs/orders` (verify
   `grep os.getenv libs/orders/python/orders` is empty).
6. Verify: `just lint` + `pyright` + tests, then one live order on kind (console-first); confirm the
   data converter is still uniform across api + workers. Update ADR-0001's cross-reference to point
   here for the refined boundary.

### Naming
`libs/appkit` for the generic kit — **not** `platform` (already the operator/control-plane app class,
`apps/platform/console`). `runtime` / `foundation` are acceptable alternatives; `appkit` chosen as
the clearest "toolkit for defining apps."
