# Checkpoint 0032 — Descriptor-driven polyglot domain adoption

**Date:** 2026-07-09
**Status:** **LANDED** — merged to `main` across 3 PRs (#45, #46, #47); tip `e8a0a98`, `just lint` green.
Polyglot domain adoption is live and proven for **Python, Java, Go, TypeScript** (workflow +
activity workers), all validated on kind+OSS with **0 Temporal Cloud executions**.

## Why

Extends checkpoint 0030 (Python foundation) / ADR-0026. Goal: make adopting an external Temporal
demo as a new **domain** seamless for a human, and erase the orders/ziggymart special-casing so
100% of domains follow one pattern. `config/domains/<domain>.yaml` is the single source of truth
driving code layout, image build, ArgoCD/Helm deploy, OSS namespace bootstrap, dashboards, and the
console trigger catalog.

## Design decisions

- **One `domain` name everywhere** (descriptor/catalog key, `libs/<domain>/`, worker path
  `apps/temporal/workers/<language>/<domain>/<profile>`, image `<domain>-worker-<profile>`, digest
  key `<domain>-<profile>`, dep group). **No `kernel` concept** (dropped as noise).
- **`namespace` is the only optional override** (defaults to `domain`). Flagship consolidated to
  `domain: orders` and dogfoods the override with `namespace: ziggymart` (fun label kept as the
  Temporal namespace; code was already `orders`, so near-zero churn).
- **Per-worker `language`** — the polyglot enabler. N workers, arbitrary worker↔queue boundaries.
  Config owns the contract; code owns registrations. `(language, profile)` names the worker dir.
- **The chart was already generic** (`range .Values.workers`); the feeders now read the descriptor
  — `build_domain_images.py` iterates descriptors; `applications.tf` `for_each` over descriptors.
- **Dockerfile by convention** (`images/<language>.Dockerfile`) + per-worker `dockerfile:` override;
  per-language build-arg adapter is the only place language-specific build knowledge lives.
  Framework (Spring Boot/Kotlin) is a build-system concern, not a Dockerfile axis.
- **Fail-fast doctor** (`verify-domain` / `verify-domains`, ERROR/WARN), step-0 gate of `adopt-domain`.
- **Journey:** `just new-domain <domain>` (stub) → edit descriptor → `just scaffold-domain <domain>`
  (idempotent, no LANG flag) → port logic → `just adopt-domain <domain>` (verify → lock → build →
  push → chart-publish → apply → bootstrap; apply is the one explicit cluster mutation).
- **Worker Deployment versioning PINNED** across all four SDKs (Python `VersioningBehavior.PINNED`,
  Java `@WorkflowVersioningBehavior(PINNED)`, Go current `DeploymentOptions{UseVersioning, Version:
  WorkerDeploymentVersion}` + `VersioningBehaviorPinned`, TS `useWorkerVersioning` +
  `setWorkflowOptions({versioningBehavior:"PINNED"})`), grounded in samples-go / samples-typescript.

## Landed as 3 PRs (rebase-merge, independently reviewed)

- **PR #45 — Foundation & symmetry** (`f63fa06`). Identity consolidation (drop kernel, ziggymart→
  orders with namespace override), domain doctor (`tomllib`/brace-aware TF parse/shared
  `temporal_namespace_from_descriptor`), worker path conform, chart-template re-derive
  (extraEnv/httpGet/slot-gates + generic `downstreamServices`). Review folded: `temporal_ui_url`
  namespace fix, stale-doc fix, dashboard re-identify, doctor robustness, dedup.
- **PR #46 — Descriptor-driven build & deploy** (`7e055af`). `build_domain_images.py` + per-language
  adapter; `applications.tf` `for_each`; digest key `<domain>-<profile>` end-to-end; fail-loud
  digest precondition; orders-api/orders-data kept as additive Applications. Review folded:
  `try(desc.workers,[])`, coalesce chart-version guard, worker-less presence gate, de-hardcode
  "orders" (k8s_namespace in descriptor), shared image-ref local, replicas default; cross-Cloud-
  namespace connection documented-deferred.
- **PR #47 — Generalization, polyglot & docs** (`e8a0a98`). Multi-domain OSS bootstrap, Grafana glob
  mount, de-default; idempotent descriptor-first scaffolder; **Go + TypeScript templates**;
  `adopt-domain` orchestration; runbook rewrite. Review (Option A — make Go/TS first-class) folded:
  filled the empty Go workflow template, fixed TS activity-name mismatch + added TS PINNED, made
  `adopt-domain` domain-scoped (no cross-domain digest churn), tracked go_sdk/ts_sdk in
  dependencies.yaml + migrated off the removed `DeploymentSeriesName` API, Go env fail-fast,
  autoscaling-when-enabled, docstring/doc/em-dash cleanup, `lint-domain-templates` (Go compile + TS
  typecheck).

## Live verification (kind + OSS, throwaway domains stripped from the diffs, 0 Cloud)

- **orders** (flagship): OrderWorkflow COMPLETED across every PR; `finalize_order` on the Java worker.
- **Go workflow** (`gowf`): HelloWorkflow COMPLETED, PINNED, activity on the activity queue.
- **TypeScript** (`tspf`): HelloWorkflow COMPLETED, activity dispatched via shared `ActivityName`, PINNED.
- **Polyglot** (`xpoly`): Python workflow + Go activity COMPLETED.
- `adopt-domain` no-churn: adopting a new domain leaves other domains' worker images unchanged in plan.
- Idempotency: `scaffold-domain` re-run = zero diff.

## Carried / deferred (documented, not blocking)

- **Cross-Cloud-namespace connection wiring** (PR #46 review #5): worker connection
  (hostPort/apiKeySecret/mtlsSecret) is single-`var.cloud_namespace` today; a domain on a *distinct*
  Cloud namespace needs per-descriptor connection wiring. Documented in an `applications.tf` note.
  Demos share orders' namespace/secret via `k8s_namespace: orders`.
- **CRD API group `autoscaling.ziggymart.io`** rename — platform-wide migration, out of scope.
- **Console free-form/JSON trigger input** — sample-inputs-only is sufficient for demos.

## Process note (for next time)

This checkpoint was originally left as an uncommitted untracked file on `main` and was lost during
the PR branch/stash reconstruction. Commit architect checkpoints promptly rather than leaving them
loose in the working tree.
