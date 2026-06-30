# ADR-0025: Centralized, tiered dependency version management (Temporal-first)

- **Status:** Accepted — landed via PR #20; mechanism validated by bringing all Tier-1
  components to latest stable and smoke-testing both Local-OSS and kind+Cloud run modes
  (see checkpoint 0024).
- **Date:** 2026-06-30
- **Related:** Extends the "one spec, no drift" pattern of ADR-0007 (the
  `config/temporal/namespaces.yaml` single source of truth). The upstream-latest checker
  is a Resolve-tier (online) tool under ADR-0013's air-gap tiering. Touches the version
  surfaces created across ADR-0018/0020/0021/0023/0024 (Alloy, ClickHouse, Prometheus,
  worker-controller) and ADR-0022 (the Python app/kernel split that spreads the SDK pin).

## Context

This repo tracks the **latest stable release of everything Temporal** — that is a stated
operating goal, not an aspiration. Yet Temporal versions were scattered across at least six
disconnected surfaces with no authoritative artifact and no cheap way to answer "are we
current?":

| Surface | Temporal content | How it's read |
|---|---|---|
| `config/dependencies.yaml` | worker-controller chart | render-deps.py → `deps.env`; TF `yamldecode()` |
| `.env` | server / admin-tools / UI tags | `compose/oss-server.yml` |
| `pyproject.toml` ×3 (root + `libs/orders` + `libs/appkit`) | `temporalio` SDK | uv resolver (static TOML) |
| `deploy/terraform/.../versions.tf` ×2 | `temporalcloud` provider | HCL `required_providers` |
| `libs/orders/proto/buf.gen.yaml` | codegen plugin | buf (static YAML) |
| (none) | the `temporal` CLI | assumed on PATH / in admin-tools |

Platform (Tier 2) versions were similarly split between `config/dependencies.yaml`,
hardcoded `docker-compose.yml` image tags, the Alloy chart, and TF provider constraints.

The friction this creates: a Temporal bump means hunting every surface, and "are we behind
upstream?" requires manually checking each registry. Both should be one command.

A key constraint makes a pure "single file drives everything" design impossible: some
consumers **cannot** read a manifest. HCL `required_providers` takes a string literal, not
a variable or file; `pyproject.toml` dependency specs are static TOML the uv resolver reads
directly; `buf.gen.yaml` plugin pins are static. You can generate *into* `.env`/shell/TF
data sources, but not into these.

## Decision

**1. One tiered manifest, extended in place.** `config/dependencies.yaml` stays the single
source of truth and keeps its filename and its existing `registry:`/`charts:`/`images:`
keys byte-compatible (the render-deps.py + cluster-layer `yamldecode()` contracts depend on
that keying). Three tier blocks are added additively, ordered by audit rigor:

- **Tier 1 — `temporal:`** everything Temporal: SDK, server/admin-tools/UI tags, CLI,
  Cloud TF provider, buf codegen plugin, worker-controller chart. Highest rigor.
- **Tier 2 — `platform:`** the stack that runs the platform: Terraform + non-Temporal
  providers, host-compose images (Prometheus/ClickHouse/OTel Collector), Alloy, Postgres.
- **Tier 3 — `code:`** application code deps (pydantic/httpx/sqlalchemy/protobuf). Loosest.

**2. Hybrid generate + verify, split on what a manifest can mechanically drive.**

- **Generate** where the wiring already exists and the target is machine-written: the kind/
  OCI delivery stack (`charts:`/`registry:`/`images:`) is unchanged — render-deps.py emits
  `deps.env`, the cluster TF layer reads the file via `yamldecode()`.
- **Verify** everywhere generation is fragile or impossible (the SDK pin ×4, the Cloud TF
  provider ×2, the buf plugins, `.env` Temporal/Postgres vars, hardcoded compose image
  tags, the Alloy chart). Those keep their native pin; `compose/scripts/versions-audit.py`
  asserts each native pin **equals** the manifest value. **Tier 1/2 drift fails** the gate;
  **Tier 3 drift warns** only. The audit is wired into `just lint` (hence `just check`), so
  drift is caught before push. Offline, stdlib + pyyaml — safe on the air-gapped gate.

  *Why verify beats generate here:* generating into static TOML/HCL would mean code-writing
  `pyproject.toml` and `versions.tf`, fragile and surprising to anyone editing them by hand.
  Verify keeps each file idiomatic and hand-editable; the manifest stays authoritative
  because the gate refuses to go green on a mismatch.

**3. `.env` Temporal vars are verify-only, not generated.** `.env` mixes version vars with
secrets and ports (`POSTGRES_PASSWORD`, `COMPOSE_PROJECT_NAME`); generating the file would
entangle the two. The audit asserts `.env`'s `TEMPORAL_*` / `POSTGRESQL_VERSION` match the
manifest instead. This also respects the deliberate "compose is a separate run mode"
boundary.

**4. The worker-controller chart version is canonical under `charts:`; the Tier-1 block
mirrors it.** The version must physically live under `charts:` (the TF layer reads it
there). The `temporal.worker_controller_chart` entry is a mirror the audit asserts equal —
so the Tier-1 view is complete without creating a second place to edit.

**5. An online upstream-latest checker, separate from the gate.**
`compose/scripts/versions-upstream.py` (`just versions-upstream`) reports Tier-1 pinned-vs-
latest-stable from PyPI, Docker Hub, GitHub releases, and the Terraform Registry. It is a
**Resolve-tier (online)** tool (ADR-0013) and is deliberately **not** in any gate — an
upstream release must never turn the offline lint red. `--strict` exits non-zero when
anything is behind, for opt-in CI/cron use.

**6. Stale version numbers in free-text comments are removed, not policed.** The
worker-controller ArgoCD Application comments hardcoded an old "0.26.0 / 1.7.0" that drifted
from the injected value. Rather than have the audit parse decorative comments (false-positive
prone), those numbers are deleted and replaced with a pointer to `config/dependencies.yaml`.

## Consequences

- **Bump-in-one-place, mostly.** For verify targets, a bump is: edit the manifest, run
  `just versions-audit` to see which native pin(s) drifted, update them, re-run until green.
  Not literally one edit, but the manifest is the authoritative checklist and the gate
  enforces convergence — no surface can be silently forgotten.
- **Audit-in-one-command.** `just versions-upstream` answers "are we on latest stable
  Temporal?" across all Tier-1 components at once.
- **The gate now fails on version drift.** Intentional: divergence between the manifest and
  a native pin is a defect, surfaced at `just lint`/`check` time, not in production.
- **New surfaces must be registered.** Adding a Temporal/platform version somewhere new
  means adding both the manifest entry and an audit check, or it escapes the gate. This is
  the maintenance cost of the verify approach; the alternative (drift goes unnoticed) is
  worse.
- **The upstream checker has registry-shape hazards.** The worker-controller Docker Hub repo
  mixes chart tags (0.x) and controller-image tags (1.x); the checker filters to the pinned
  major to compare like-for-like. Revisit if the chart line itself changes major.

## Alternatives considered

- **Generate into everything (push-only).** Rejected: code-writing `pyproject.toml` and
  `versions.tf` is fragile and fights uv/Terraform idioms; the SDK spec and provider
  constraint are meant to be hand-readable.
- **A new top-level `versions.yaml`.** Rejected: would break the existing render-deps.py /
  `yamldecode()` contract for no benefit over extending the file that already owns that role.
- **Renovate/Dependabot.** Complementary, not a substitute — those automate *bumping*, but
  this repo's need is a single authoritative artifact + an at-a-glance Temporal-latest audit
  across heterogeneous surfaces (charts, images, TF, buf) that Renovate doesn't unify. Can
  be layered on later against the same manifest.
