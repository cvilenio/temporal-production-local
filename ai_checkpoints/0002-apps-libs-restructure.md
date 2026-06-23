# 0002 — apps/ + libs/ restructure (type-first, polyglot-legible)

- **Status:** Landed in working tree, verified (uv sync / ruff / format / pyright / import
  smoke green). Uncommitted — left as a reviewable diff on top of commit `28fda28`.
- **Date:** 2026-06-23

## What changed (on top of 0001)

Resolved the three open layout questions from 0001 via dialogue and restructured:

- **Top level is now `apps/` + `libs/`** — the recognizable deployables-vs-library split.
- **`apps/` grouped by deployment class** (type-first, so "all my workers" / "all business
  apps" is one `ls`):
  - `apps/temporal/` — Temporal platform: `workers/python/{workflow,activity}`, `codec-server/python`
  - `apps/business/` — customer-like client/gateway: `orders-api/python`
  - `apps/demo/` — not required in prod: `console/python`, `mock-api/python`
- **`libs/orders/python/orders/`** — shared code (was `kernels/`), package `orders`. Use
  case above language; `libs/` chosen for cross-language legibility (`src/` is
  intra-package; `kernel/` is jargon).
- **`images/python.Dockerfile`** — one configurable image per language (was `app.Dockerfile`).
- Rewired: uv workspace member → `libs/orders/python`; regenerated `uv.lock`; pyright roots;
  Dockerfile `COPY libs`; all six compose `APP_PATH` + dockerfile paths. Updated
  `docs/ARCHITECTURE.md`, ADR-0001, README.

Code imports unchanged (`from orders…`) — only paths/wiring moved. History preserved
via `git mv`.

## Decisions (promoted into ADR-0001)

Type-first `apps/{temporal,business,demo}` + `libs/` naming; centralized per-language image;
shared root uv workspace; orders-api classified as customer-like business app (a client),
not a worker.

## Next

- Optional: commit this restructure (clean diff on `28fda28`).
- Then resume the deploy layer: finish Terraform/ArgoCD wiring, write remaining app Helm
  charts (orders-api/console/mock-api/codec-server/observability), confirm Worker Controller
  CRD field names, real AEAD codec, mark `OrderWorkflow` PINNED + wire data-converter codec.
