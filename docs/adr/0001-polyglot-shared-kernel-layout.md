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

- `kernels/<lang>/` — reusable library code (workflows, activities, clients, telemetry,
  app/worker factories). For Python this is the uv workspace package `orders-kernel`, which
  owns the concrete dependency versions.
- `apps/<type>/<lang>/` — thin deployment units. Types: `workers/`, `orders-api/`,
  `codec-server/`, `console/`, `mock-api/`. Each app imports its kernel and starts one
  thing.
- `images/app.Dockerfile` — one configurable image; build args select the dependency group
  and entrypoint. The kernel is always installed (the "base"); the app dir is copied last
  (the "definition").

This mirrors the official samples' structure in every SDK (see ARCHITECTURE.md).

## Consequences

- Adding a language = a new `kernels/<lang>/` + `apps/*/<lang>/`; no reshuffle.
- App definitions stay tiny; library versions have a single source of truth per language.
- The console keeps its own `app.*` package and a duplicated `TaskQueue`; a cross-cutting
  shared definition can be extracted later if churn warrants.
- pyright uses per-app execution environments; the kernel resolves via the editable
  workspace install.
