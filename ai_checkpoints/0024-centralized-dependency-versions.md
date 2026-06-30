# 0024 — Centralized, tiered dependency version management

- **Status:** **Landed + verified live** (both run modes) — merged via PR #20 (rebase).
- **Date:** 2026-06-30
- **ADRs:** ADR-0025 (the decision). Extends ADR-0007 ("one spec, no drift"); upstream checker
  is Resolve-tier per ADR-0013.

## Done this session

- **Restructured `config/dependencies.yaml`** into tiered blocks, additively:
  - `temporal:` (Tier 1) — SDK, server/admin-tools/UI, CLI (new pin), Cloud TF provider,
    buf plugin, worker-controller chart mirror.
  - `platform:` (Tier 2) — Terraform + non-Temporal providers, host-compose image tags,
    Alloy, Postgres.
  - `code:` (Tier 3) — pydantic/httpx/sqlalchemy/protobuf (report-only).
  - Existing `registry:`/`charts:`/`images:` kept **byte-compatible** so render-deps.py and
    the cluster TF `yamldecode()` contracts are untouched. Worker-controller version stays
    canonical under `charts:`; Tier-1 carries a mirror the audit asserts equal.
- **`compose/scripts/versions-audit.py`** (new) — VERIFY half of the generate/verify split.
  Reads every native pin (4 pyproject locations, `.env`, 3 TF files, `buf.gen.yaml`, Alloy
  Chart/values, `docker-compose.yml`, host-apptier) and asserts it equals the manifest.
  Tier-1/2 drift → exit 1; Tier-3 → warn. **SKIPs absent files** (e.g. git-ignored `.env` on
  a fresh clone / CI) rather than crashing. Offline, stdlib + pyyaml.
- **`compose/scripts/versions-upstream.py`** (new) — Tier-1 pinned-vs-latest-stable from
  PyPI / Docker Hub / GitHub releases / Terraform Registry. Online (Resolve tier), honors
  `GITHUB_TOKEN`, `--strict` for non-zero exit. Worker-controller check filters Docker Hub
  tags to the pinned major (the repo mixes chart 0.x and image 1.x tags).
- **justfile** — added `versions-audit` + `versions-upstream` recipes; wired
  `versions-audit` into `lint:` (so `just check` gates on version drift).
- **De-versioned the stale ArgoCD comments** in
  `deploy/argocd/applications/temporal-worker-controller{,-crds}.yaml`; replaced with a
  pointer to `config/dependencies.yaml`.
- **Brought all Tier-1 components to latest stable** (validating the mechanism by using it):
  - `temporalio` SDK `>=1.28,<1.29` → `>=1.29,<1.30` (3 pyproject files + `uv.lock` 1.28→1.29).
  - `temporalio/server` + `admin-tools` `1.31.0` → `1.31.1` (`.env`).
  - `temporalio/ui` `2.50.0` → `2.51.1` (`.env`).
  - CLI `v1.7.2`, Cloud TF provider `~> 1.5`, worker-controller chart `0.27.0` already current.
  - Post-bump `just versions-upstream` shows **all Tier-1 UP-TO-DATE**; `just versions-audit`
    green 35/35.
- **Smoke-tested both run modes on the bumped versions:**
  - **Local OSS** (`just up`): server `1.31.1` (`ServerVersion=1.31.1`, Healthy), UI `2.51.1`
    (HTTP 200), admin-tools ran schema + namespace + search-attrs, SDK `1.29.0` apps healthy.
  - **kind + Cloud** (`just platform-up`): rebuilt worker images against the new lock; new
    workflow + activity pods on SDK `1.29.0`; worker-controller (chart 0.27.0) rolled them
    (`WorkerDeployment` CURRENT==TARGET on the new build IDs); Temporal Cloud shows both
    deployments' CurrentVersionBuildID = the new builds; **one** order workflow ran the full
    saga to `completed` (1 Cloud execution).

## Open questions

- None blocking. `.env` is git-ignored, so its Temporal/Postgres pins aren't committed — the
  manifest is authoritative and the audit verifies a local `.env` / SKIPs when absent.

## Next

1. (Optional) Layer Renovate/Dependabot on top of the manifest for automated bump PRs.
2. (Optional) CI lane running `just versions-upstream --strict` on a schedule (cron) to flag
   upstream drift — kept OUT of the offline gate by design.
