# ADR-0001: Polyglot shared-kernel monorepo layout

- **Status:** Accepted
- **Date:** 2026-06-23

## Context

The repo began as a single-language (Python) demo with top-level service directories and
one root `pyproject.toml`. It must grow to host workers/activities in Go, TypeScript, and
Java, while keeping each deployable app small and easy to reason about. The maintainer's
stated preference is a shared-kernel model: common code in one place, each app a shallow
configuration on top — proven previously with a shared base image and thin Python apps.

## Decision

Organize the repo as **shared kernels per language + thin apps grouped by type**:

- `libs/<use-case>/<lang>/` — reusable library code per use case (workflows, activities,
  clients, telemetry, app/worker factories). Use case sits **above** language
  (`libs/orders/python`) so a domain's polyglot pieces stay together, mirroring `apps/`.
  The importable package is just `orders`, installed editable via the uv workspace, so
  imports are flat `from orders…` regardless of filesystem depth. (`libs/` over
  `kernel/`/`src/`: the recognizable apps-vs-library signal across languages.)
- `apps/<class>/<app>/<lang>/` — thin deployment units, grouped by **deployment class**:
  `temporal/` (workers, codec-server), `business/` (the orders-api client/gateway), `demo/`
  (console, mock-api — not required to run Temporal in prod). Each app imports the kernel
  and starts one thing.
- `images/<lang>.Dockerfile` — one configurable image per language; build args select the
  dependency group and entrypoint. The kernel is always installed (the "base"); the app dir
  is copied last (the "definition").

This mirrors the official samples' structure in every SDK (see ARCHITECTURE.md).

## Consequences

- Adding a language = a new `libs/<use-case>/<lang>/` + `apps/*/*/<lang>/`; no reshuffle.
- App definitions stay tiny; library versions have a single source of truth per language.
- The console keeps its own `app.*` package and a duplicated `TaskQueue`; a cross-cutting
  shared definition can be extracted later if churn warrants.
- pyright uses per-app execution environments; the kernel resolves via the editable
  workspace install.
