# Checkpoint 0033 — Ruby & .NET polyglot workers + runtime-version flexibility

**Date:** 2026-07-10
**Status:** **ACCEPTED — merging via PR #49** (branch `polyglot-ruby-dotnet`).
Extends checkpoint 0032 (Python/Java/Go/TypeScript). Ruby and .NET are now first-class descriptor-driven
domain worker languages, and per-worker language-runtime version is a first-class, uniform mechanism.
Validated on kind+OSS with **0 Temporal Cloud executions** (3 OSS executions total across the task).

## Why

Customers run Ruby and .NET. Checkpoint 0032 landed the big-four SDKs behind one descriptor
(`config/domains/<domain>.yaml`); this extends the same machinery to two more SDKs without special-casing.
Separately, the 0032 design pinned each language runtime only inside a single `images/<language>.Dockerfile`
(`FROM` / `ARG`), with the per-worker `dockerfile:` override as the only escape hatch — so "Python 3.11 for
domain A, 3.13 for domain B" or ".NET 8 vs .NET 10" required authoring a bespoke Dockerfile. This checkpoint
generalizes Go's existing `ARG GO_VERSION` pattern into a uniform, optional per-worker `runtime_version`.

## Design decisions

- **`runtime_version` (optional, per worker)** — generalizes `images/go.Dockerfile`'s `ARG GO_VERSION`
  to every language. Each Dockerfile takes a base-image version ARG whose default equals the prior pin
  (Python `3.12`, Java `17`, Go `1.26`, Node `22`, Ruby `3.3`, .NET `8.0`) — so omitting the field is a
  no-op. `build_domain_images.py` maps `runtime_version` → the language's ARG. Runtime pins are now tracked
  in `config/dependencies.yaml` under `platform.runtimes` and asserted by `versions-audit.py` (audit parity
  with SDK pins; previously runtimes had none). Proven: Python `3.13` and .NET `net10.0` builds.
- **.NET runtime version is a THREE-place agreement, threaded from one value.** `net8.0` vs `net10.0` is not
  just an image tag — it is the compiled `TargetFramework`, which must match the SDK build image and the
  runtime image. `runtime_version: net8.0` maps (via `dotnet_runtime_parts`) to image tag `8.0` **and** TFM
  `net8.0`; the Dockerfile passes `DOTNET_VERSION` (both `sdk:`/`runtime:` FROMs) and `TARGET_FRAMEWORK`
  (`dotnet restore`/`publish -p:TargetFramework=…`), and `Directory.Build.props` honors the override with an
  `net8.0` fallback. A `net10.0` build was proven to flip base image and TFM together — closing the silent
  mismatch trap. Ruby is simple by comparison (`ruby:${RUBY_VERSION}-slim` tag only).
- **.NET layout is idiomatic central-package-management** (matches `samples-dotnet` + ADR-0025 philosophy):
  root `Directory.Build.props` (one `TargetFramework`, `Temporalio` ref) + `Directory.Packages.props` (CPM
  pin `Temporalio` 1.16.0) + workflow-scoped `.editorconfig` (silences analyzer rules that fight workflow
  code) + `.workflow.cs` extension. Per-project `.csproj` stays near-empty.
- **Ruby is self-contained per worker dir** (like Go): per-worker `Gemfile`/`Gemfile.lock`, path-gem to
  `libs/<domain>/ruby`, no workspace manifest. `Gemfile.lock` generated idempotently (skip-if-exists).
- **Worker Deployment versioning PINNED** in both new SDKs, grounded in current sample code:
  Ruby `workflow_versioning_behavior Temporalio::VersioningBehavior::PINNED` +
  `Temporalio::Worker::DeploymentOptions.new(version: WorkerDeploymentVersion.new(deployment_name:, build_id:),
  use_worker_versioning: true)`; .NET `[Workflow(…, VersioningBehavior = VersioningBehavior.Pinned)]` +
  `WorkerDeploymentOptions { Version = new WorkerDeploymentVersion(name, buildId), UseWorkerVersioning = true }`.
  (Worker-level `DefaultVersioningBehavior` dropped as redundant once every workflow declares Pinned explicitly.)
- **.NET cross-SDK payload interop = camelCase converter.** .NET's default `System.Text.Json` converter is
  PascalCase + case-sensitive, so it would not bind the `{"name":…}` JSON the console `sample_inputs` catalog
  and the other five SDK templates use (surfaced live as `"Hello, !"`). The template ships a
  `CamelCasePayloadConverter : DefaultPayloadConverter` (camelCase + case-insensitive) wired into the worker
  client `DataConverter`, per the documented "use camelCase converter if interoperating with other SDKs" guidance.
- **SDK/env grounding corrected against the repo, not the docs:** workers read `TEMPORAL_DEPLOYMENT_NAME`
  (not the SDK-doc `TEMPORAL_WORKER_DEPLOYMENT_NAME`) + `TEMPORAL_WORKER_BUILD_ID`, matching the chart and the
  existing Go/TS/Python templates.
- **No chart/Terraform generalization needed** — ruby/dotnet ride the existing non-python `language` path in
  `applications.tf`; the `domain-workers` chart already derives `command`/`startupProbe` generically. Zero
  `templates/charts/**` change, so no chart-version bump was required.

## Landed as PR #49 (branch `polyglot-ruby-dotnet`, 2 commits, rebase-merge)

Built captain/crew (Claude = architect/gatekeeper, Cursor = implementer) across 5 gated phases:
0. Runtime-version foundation retrofit to the existing four languages (zero regression).
1. Ruby (Dockerfile, templates, allowlists, adapter, scaffolder, doctor, deps, lint).
2. .NET (same + CPM + `.editorconfig` + TFM threading).
3. Live kind+OSS validation — surfaced the Ruby `/libs`-at-runtime + mTLS gaps and the .NET camelCase gap.
4. Fold fixes into templates, docs/ADR, strip throwaways, PR, temporal-aware review folded (commit `091685a`).

## Live verification (kind + OSS, throwaway domains stripped, 0 Cloud)

- **rubywf** (throwaway): HelloWorkflow COMPLETED, PINNED (`orders/rubywf-workflow-ruby`), activity on
  `rubywf-activity-task-queue`.
- **dotwf** (throwaway): HelloWorkflow COMPLETED, PINNED (`orders/dotwf-workflow-dotnet`); after the camelCase
  fix, returns `"Hello, Temporal!"` (was `"Hello, !"`), activity on `dotwf-activity-task-queue`.
- **Runtime-version:** Python `3.13` and .NET `net10.0` builds proven (net10.0 flips image tag + TFM together).
- **No-churn:** adopting the new domains left existing domains' worker image digests unchanged in plan.
- **Cumulative footprint:** 3 workflow executions, all OSS, 0 Temporal Cloud.

## Carried / deferred (documented, not blocking)

- **Review nits not folded:** Ruby TLS partial-cert guard (only-some-cert-paths-set); Ruby Dockerfile copies
  the whole `libs/` tree rather than the domain's ruby lib only (image bloat); `lint_domain_templates.py`
  dotnet check hardcodes `8.0`; `MyActivities` generic naming. All robustness/style, none correctness.
- **Console `/domain-trigger` on kind+OSS** needs `TEMPORAL_TRIGGER_TLS=true` on the host plane (certs already
  mounted); CLI used for execution proof this round. Documented in the runbook.
- **Cross-Cloud-namespace connection wiring** — still single-`var.cloud_namespace` (carried from 0032).

## Process note

Live validation (Phase 3) is what caught the Ruby runtime gaps and the .NET payload-casing bug — the Phase 1/2
"image builds" gates proved compile/build, not *worker runs*. Keep the live-run gate distinct from the
build gate for every new language. Checkpoint committed promptly per 0032's note.
