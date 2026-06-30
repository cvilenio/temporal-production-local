# 0024 — Centralized, tiered dependency version management

- **Status:** **Landed** — audit passes against current pins; upstream checker live. Not yet
  committed/PR'd.
- **Date:** 2026-06-30
- **ADRs:** ADR-0025 (the decision). Extends ADR-0007 ("one spec, no drift"); upstream checker
  is Resolve-tier per ADR-0013.

## Done this session

- **Restructured `config/dependencies.yaml`** into tiered blocks, additively:
  - `temporal:` (Tier 1) — SDK, server/admin-tools/UI, CLI (new pin `v1.7.2`), Cloud TF
    provider, buf plugin, worker-controller chart mirror.
  - `platform:` (Tier 2) — Terraform + non-Temporal providers, host-compose image tags,
    Alloy, Postgres.
  - `code:` (Tier 3) — pydantic/httpx/sqlalchemy/protobuf (report-only).
  - Existing `registry:`/`charts:`/`images:` kept **byte-compatible** so render-deps.py and
    the cluster TF `yamldecode()` contracts are untouched. Worker-controller version stays
    canonical under `charts:`; Tier-1 carries a mirror the audit asserts equal.
- **`compose/scripts/versions-audit.py`** (new) — VERIFY half of the generate/verify split.
  Reads every native pin (4 pyproject locations, `.env`, 3 TF files, `buf.gen.yaml`, Alloy
  Chart/values, `docker-compose.yml`, host-apptier) and asserts it equals the manifest.
  Tier-1/2 drift → exit 1; Tier-3 → warn. Offline, stdlib + pyyaml. Passes 35/35 against
  current pins.
- **`compose/scripts/versions-upstream.py`** (new) — Tier-1 pinned-vs-latest-stable from
  PyPI / Docker Hub / GitHub releases / Terraform Registry. Online (Resolve tier), honors
  `GITHUB_TOKEN`, `--strict` for non-zero exit. Worker-controller check filters Docker Hub
  tags to the pinned major (the repo mixes chart 0.x and image 1.x tags).
- **justfile** — added `versions-audit` + `versions-upstream` recipes; wired
  `versions-audit` into `lint:` (so `just check` gates on version drift).
- **De-versioned the stale ArgoCD comments** in
  `deploy/argocd/applications/temporal-worker-controller{,-crds}.yaml` (they hardcoded an
  old 0.26.0/1.7.0); replaced with a pointer to `config/dependencies.yaml`.

## Finding — repo is behind upstream on several Tier-1 components

`just versions-upstream` (run this session) flags (not fixed here — out of scope for the
mechanism change):

- `temporalio` SDK pinned `>=1.28,<1.29` — latest stable **1.29.0**.
- `temporalio/server` + `admin-tools` `1.31.0` — latest **1.31.1**.
- `temporalio/ui` `2.50.0` — latest **2.51.1**.
- CLI `v1.7.2`, Cloud TF provider `~> 1.5`, worker-controller chart `0.27.0` — up to date.

## Open questions

- None blocking. The SDK bump to 1.29 crosses the `<1.29` ceiling in 4 pyproject files —
  do it as its own change (touch the manifest + the 4 pins, `just versions-audit` to confirm,
  re-lock `uv.lock`).

## Next

1. Bump the 4 behind-upstream Temporal components (separate, focused change per above).
2. Optionally layer Renovate/Dependabot on top of the manifest for automated bump PRs.
3. Optional CI lane: `just versions-upstream --strict` on a schedule (cron), not the gate.
