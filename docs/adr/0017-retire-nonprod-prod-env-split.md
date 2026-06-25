# ADR-0017: Retire the nonprod/prod environment split — one namespace per domain

- **Status:** Accepted
- **Date:** 2026-06-25
- **Related:** ADR-0005 (connection profiles), ADR-0007 (namespace provisioning — spec shape),
  ADR-0008 (auth identities), ADR-0004 (worker versioning). Supersedes the env axis those
  assumed. Recorded in `ai_checkpoints/0015`.

## Context

The repo provisioned two Cloud namespaces per business domain — `ziggymart-nonprod` and
`ziggymart-prod` — modelling a nonprod/prod environment axis. The repo's primary purpose is
to **demonstrate Temporal features**, so the question is whether that env split earns its
place by demonstrating a capability impossible within a single environment.

It does not. Worker versioning (Build IDs, ramping/rainbow, pinned vs auto-upgrade),
retries/timeouts, schedules, retention, search attributes, APS/OPS tuning, and the codec are
all **single-namespace** concerns. The Temporal capabilities that genuinely require namespace
multiplicity are:

- **Nexus** — cross-namespace RPC, which is the **domain** axis (orders ↔ payments), never
  nonprod↔prod.
- **Multi-region HA / failover** — the **region** axis (per-namespace HA), not env.

A nonprod→prod *promotion* is a CI/CD artifact-movement idea, not a Temporal feature; and
Temporal worker versioning is explicitly designed to **ramp a new Build ID within the live
namespace**, which is the production rollout pattern. So the env axis added cost (a second
namespace, profile, SA/key, and the `<domain>-<env>` keying threaded through both Terraform
layers) with no Temporal payoff.

## Decision

Retire the environment axis. One Cloud namespace per **domain**, named by the bare
`<domain>` (e.g. `ziggymart`; future `payments`). The repo models a single,
production-shaped environment ("all production") — a small, honest conceit for a disposable
workbench we develop against. Customer rollouts still use env-separated namespaces; that
guidance lives in `docs/SHIP_PLAN.md`, distinct from the repo's own model.

Concretely:

- **Spec** (`config/temporal/namespaces.yaml`): drop `domains.<d>.environments.{…}` and the
  `oss.environment` selector; retention + search attributes are domain-level (single
  prod-shaped retention, 30 days). Cloud and OSS namespace names now **converge** on the bare
  `<domain>` (OSS already used it).
- **Cloud Terraform**: `cloud_overlay`, the `for_each`, and all outputs are keyed by
  `<domain>`. The `cloud-namespace` module is unchanged — domain-vs-env was always a caller
  concern in `namespaces.tf`.
- **Cluster Terraform**: `var.cloud_env` → `var.cloud_namespace` (default `ziggymart`).
- **Profiles**: `.secrets/keys/cloud-{nonprod,prod}.env` → a single `.secrets/keys/cloud.env`
  (per-domain `cloud-<domain>.env` is the forward path when a second domain's workers run on
  kind).
- **Delivery progression** (workloads layer): release progression is worker versioning within
  the namespace + git-tag/digest pinning — not a nonprod→prod artifact promotion.
- **Auth divergence** (ADR-0008) is now the per-domain axis.

The execution required a **destroy + recreate** of the existing Cloud namespaces (Temporal
Cloud namespaces are identity-by-name and cannot be renamed), via the `cloud-namespace`
module's `prevent_destroy` escape hatch — an irreversible loss of those namespaces' history,
accepted because they were effectively empty and the workbench is rebuildable.

## Consequences

- Simpler model; one fewer axis threaded through the spec, both Terraform layers, profiles,
  charts, and docs. Cloud/OSS naming converges → even less drift surface (ADR-0007).
- The axes that *do* carry Temporal value are now first-class: **domain** (add a spec + overlay
  entry → its own namespace + SA/key → Nexus + per-domain auth) and **region** (per-domain
  `regions` in the overlay → multi-region HA).
- The console's `is_self` "this" badge stays meaningful — it now distinguishes the domain the
  cluster points at (`ziggymart`) from future domains, rather than nonprod from prod.
- No within-env Temporal capability is lost; worker versioning is demonstrated the way it is
  meant to be used (ramp within the live namespace).
