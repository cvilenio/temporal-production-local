# 0005 — Shared namespace/search-attr spec + production-grade OSS provisioning

- **Status:** **LANDED in working tree (2026-06-24), not yet committed.** Cloud `terraform plan`
  = **No changes** after the refactor; local OSS server+bootstrap chain brought up and the three
  custom search attributes verified registered from the spec, then torn down.
- **Date:** 2026-06-24

## Context

Checkpoint 0004 provisioned Temporal **Cloud** the production-grade way (layered Terraform,
declarative namespaces + search attributes + least-privilege keys). Local **OSS** had the same
feature set but via ad-hoc Compose init containers with the search-attribute set **hardcoded
inline** — duplicated against the Cloud `terraform.tfvars`, free to drift, and not how
customers run self-hosted Temporal. Goal this session: kill the drift with one shared spec,
keep OSS provisioning in the delivery plane (ADR-0002), and design production-grade OSS auth.

## Done this session (Workstream A)

### Shared, backend-agnostic spec — single source of truth
- New `config/temporal/namespaces.yaml`: domain, custom **search attributes**
  (`OrderId`/`TraceId` Text, `OrderStatus` Keyword), per-env retention, and an `oss` block
  picking which env the local cluster mirrors (`nonprod`). Both backends read THIS file.

### Cloud Terraform consumes the spec (zero state churn)
- `layers/cloud/main.tf` now derives the namespace map in `locals` via
  `yamldecode(file(".../config/temporal/namespaces.yaml"))`, flattening domains×envs into the
  `<domain>-<env>` keys and **merging a Cloud-only overlay** on top.
- `variables.tf`: replaced the full `namespaces` map with a slim `cloud_overlay`
  (service account, API key, regions only — the fields with no OSS analog). `terraform.tfvars`
  + `.example` shrunk to match.
- **No-churn gate met:** derived keys/values are byte-identical, so `terraform plan` reports
  **"No changes"** (namespaces are `prevent_destroy`). Verified live against Cloud nonprod+prod.

### OSS consumes the spec (no more hardcoded attrs, no yq/jq in-container)
- `compose/scripts/render-oss-bootstrap.py` (host; `pyyaml`, added to the `dev` group) renders
  the spec → `config/temporal/.generated/oss-bootstrap.env` (git-ignored). Run by `poe up`.
- `compose/scripts/bootstrap-search-attributes.sh` sources that file and loops
  `temporal operator search-attribute create`. Replaces the inline hardcoded block in
  `compose/oss-server.yml`; the bootstrap service now mounts `./compose/scripts` + `./config/
  temporal` (ro) and is **labeled the non-prod local convenience**.

### Docs / ADRs
- `docs/adr/0007-oss-namespace-provisioning.md` (Accepted): shared spec is source of truth;
  Cloud→Terraform, OSS→delivery plane; why the control-plane vs data-plane asymmetry is
  inherent; kind uses an Argo PostSync Job, not init containers.
- `docs/adr/0008-oss-authn-authz.md` (Proposed/design): production-grade OSS auth = mTLS
  (cert-manager) + JWT (`defaultJWTClaimMapper`/`defaultAuthorizer` + OIDC/Dex JWKS), delivery
  plane, kind-only. Includes the **ArgoCD sync-wave / PostSync hook / health-gate** ordering.
- `docs/RUNMODES.md`: new "One spec, no Cloud↔OSS drift" section + spec in the Files list.

## Verified live
- Renderer ✓; `terraform fmt`/`validate` ✓; **`terraform plan` = No changes** ✓; `docker
  compose config` shows the rewired service + both mounts ✓.
- Brought up `postgresql→admin-tools→temporal→create-namespace→search-attributes`; bootstrap
  logs show all three attrs added; `operator search-attribute list` confirms `OrderId`/
  `TraceId`/`OrderStatus` on `ziggymart`. Stack torn down (`down -v`). `ruff` clean.

## Decisions
- Shared spec holds only logical config; each backend has a thin adapter (Cloud: `<domain>-<env>`
  + overlay; OSS: bare `<domain>`). See ADR-0007.
- OSS provisioning stays delivery-plane — **no** Terraform `local-exec` path (rejected).
- User overruled "leave OSS no-auth": OSS auth IS wanted, production-grade — but kind-only,
  delivery plane, separate workstream. See ADR-0008.

## Open questions
- OSS `oss.environment` defaults to `nonprod` retention for the local namespace — confirm that's
  the desired local mirror, or add a dedicated dev retention.
- Namespace *name* (`ziggymart`) still set via `DEFAULT_NAMESPACE` env in compose (not spec-fed);
  fine as a stable constant, but could be spec-driven for full symmetry.

## Next
- **Workstream B (with kind):** Argo-managed bootstrap Job rendered from the same spec
  (PostSync hook in the `temporal-server` chart) — replaces the Compose init containers.
- **Workstream C (with kind):** build OSS auth per ADR-0008 (cert-manager + OIDC + mTLS/JWT).
- Build `layers/cluster` (kind + ArgoCD + credential Secret) — still the prerequisite for B/C.
- Promote 0004's pending decisions (auth/state/run-modes/secrets) to ADRs.
