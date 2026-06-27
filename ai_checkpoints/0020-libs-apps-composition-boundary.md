# 0020 — /libs vs /apps: domain core vs application composition boundary

- **Status:** **DECISION LANDED (ADR-0022); migration NOT yet started.** This checkpoint is the
  handoff to execute the migration in a fresh session.
- **Date:** 2026-06-26
- **ADRs:** **ADR-0022** (new) — refines ADR-0001 (shared-kernel layout). Builds on ADR-0005
  (connection profiles), ADR-0004 (worker-deployment identity = contract), ADR-0021 (data
  converter = contract).

## Why

ADR-0001's "fat shared kernel + thin apps" put all composition (env `Settings`, DI container,
worker profiles, `run_worker`, FastAPI factory) in `libs/orders` — so the "lib" reads env, builds
clients, and owns process lifecycle, none of which is domain logic. We separated the concerns into
three classes and decided where each lives. See ADR-0022 for the full reasoning.

## Decisions (ADR-0022)

- **Three classes:** (1) domain core → `libs/<domain>`; (2) app definition/assembly → `/apps`;
  (3) reusable app-definition blocks, split into **3a** generic kit and **3b** domain composition.
- **Invariant:** `/libs` = reusable + not-deployable (domain kernels **and** a generic kit
  `libs/appkit`); `/apps` = the deployable assembly.
- **3a → `libs/appkit`** (domain- and app-agnostic): Temporal client builder (bakes in the data
  converter contract), SQL engine factory, telemetry bootstrap, `run_worker` loop,
  deployment-config builder, settings field-groups.
- **3b → per-app** (no shared module): provider *lifetime* (`Singleton`/`Resource`/`Factory`) is a
  process policy, so each app's composition root wires the ports it uses.
- **policy vs. contract:** per-app freedom is for policy; contracts that must be uniform
  (data converter, worker-deployment identity/versioning, task-queue/namespace/search-attribute
  keys) stay shared and are consumed, never re-decided. The data converter is the trap — looks like
  wiring, is a contract.
- **Guardrails:** domain ports stay lifecycle-agnostic (explicit `open/aclose`, no globals); kit
  owns error-prone construction, app owns lifecycle; settings are composable field-groups.

## Open questions

- Kit name: `libs/appkit` chosen (not `platform` — taken by the console app class). `runtime`/
  `foundation` acceptable; confirm before creating the package if a strong preference exists.
- FastAPI app skeleton/lifespan: thin `appkit` helper vs. fully in the orders-api app — decide
  during Phase 3 (lean: keep the skeleton in the app; only extract if a 2nd web app appears).

## Next — execute the migration (ADR-0022 § Migration plan has the file-by-file table)

1. Create `libs/appkit/python` as a uv workspace member (add to root `pyproject.toml` members +
   `[tool.uv.sources]`; pyright executionEnvironments).
2. Extract 3a into `appkit`: `build_temporal_client(profile)` (with the converter contract baked
   in), SQL engine factory (`db/engine.py`), telemetry bootstrap, settings field-groups, `run_worker`
   + deployment-config builder.
3. Move the orders-api app (`api.py` routes + lifespan) into `apps/business/orders-api/python/`.
4. Give each app a composition root: wire ports from `appkit` + `libs/orders` ports + its own
   settings, choosing provider lifetimes locally. Delete `containers.py`/`resources.py`.
5. Push `WORKER_PROFILES` per-app; remove composition remnants from `libs/orders`. Verify
   `grep -rn "os.getenv\|os.environ" libs/orders/python/orders` is empty.
6. Verify: `just lint` + `pyright` + tests, then one live order on kind (console-first); confirm the
   data converter is still uniform across api + workers. Update ADR-0001's cross-ref to ADR-0022.

Do as its own PR (or a short series of phase PRs), separate from the protobuf work
(checkpoint 0019 / ADR-0021), which landed first.
