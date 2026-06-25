# 0015 — Retire the nonprod/prod env split (domain-only namespaces)

- **Status:** **LANDED + LIVE-VALIDATED ON KIND+CLOUD (2026-06-25).** The rename executed;
  orders run end-to-end on `ziggymart`.
- **Date:** 2026-06-25
- **ADRs:** **ADR-0017** (new — retire env split). Update notes on ADR-0007 (spec domain-only)
  and ADR-0008 (auth divergence is the domain axis).

## Why

The nonprod/prod env axis demonstrates no Temporal feature — versioning, retries, schedules,
retention, APS, codec are all single-namespace. The axes that need namespace multiplicity are
**domain** (Nexus, per-domain auth) and **region** (multi-region HA), neither of which is env.
So the repo collapses to one production-shaped namespace per domain (`ziggymart`; future
`payments`), an honest conceit for a disposable workbench. Worker versioning becomes *more*
faithful (ramp a Build ID within the live namespace = the real production pattern). See
ADR-0017.

## Done this session (code + docs)

- **Spec** (`config/temporal/namespaces.yaml`): dropped `environments.{nonprod,prod}` + the
  `oss.environment` selector; domain-level `retention_days: 30` + search attributes. Cloud and
  OSS namespace names now converge on the bare `<domain>`. `render-oss-bootstrap.py` reads
  `domain["retention_days"]` directly.
- **Cloud TF**: `namespaces.tf` iterates domains (no `domain×env` flatten); `cloud_overlay`,
  `terraform.tfvars`(+example), and all `outputs.tf` maps re-keyed by `<domain>`; SA/key names
  dropped the `-nonprod` suffix (`orders-workers`, `orders-api`). `cloud-namespace` module
  unchanged except the `prevent_destroy` comment. `observer.tf` unchanged.
- **Cluster TF**: `var.cloud_env` → `var.cloud_namespace` (default `ziggymart`);
  `remote-state.tf` keys outputs by it.
- **Profiles**: `.secrets/keys/cloud-{nonprod,prod}.env` → single `.secrets/keys/cloud.env`;
  re-pointed `pyproject.toml` (`up-cloud-kind`), `justfile` (`release-worker-deployments`),
  `.secrets/README.md`, `docs/runbooks/argocd-stuck-sync.md`, the cloud-layer README.
- **Charts**: `orders-workers`/`orders-api` values `temporalNamespace` placeholders →
  `ziggymart.<account-id>` (cosmetic; cluster injects the real handle).
- **Docs**: README (provision + profile derivation by domain), RUNMODES (matrix profile row,
  files bullet, namespace-convention block → domain), workloads README (release progression =
  in-namespace worker versioning, not env promotion), SHIP_PLAN (note: repo = single env,
  customer rollouts still use envs), ADR-0007/0008 update notes, ADR-0017 (new).

## Verification

- **Static (DONE):** `terraform validate` green on cloud + cluster layers;
  `render-oss-bootstrap.py` emits `OSS_NAMESPACE=ziggymart` + retention 30; `poe lint`;
  grep sweep — no stray `nonprod`/`ziggymart-prod` except SHIP_PLAN's intentional customer
  guidance, and the env-ADR refs correctly point at ADR-0017 (console ADR-0015 left intact).
- **Live (DONE):** flipped `prevent_destroy`, applied, restored. **Gotcha:** Temporal Cloud
  **does not support deleting a search attribute**, so the destroy of the old namespaces failed
  on their `temporalcloud_namespace_search_attribute` children (`deleting an existing search
  attribute is not supported`). Fix: `terraform state rm` the 6 old SA child resources (they go
  with the namespace via `DeleteNamespace`, which *is* supported), then apply — the old
  namespaces destroyed cleanly. `ziggymart` created fine on the first apply. Re-derived
  `.secrets/keys/cloud.env`, ran `just up-cloud-kind` (console now points at `ziggymart.evvjb`,
  probe healthy) + `just platform-up` (k8s key Secrets + Applications updated; ArgoCD rolled
  the orders-api/worker pods). Verified: console shows the single `ziggymart` namespace
  (aws-us-east-1, healthy); a Happy Path order ran to **completed**; observer counts 1
  completed workflow in `ziggymart`.
- **Non-blocking observation:** workers log a `RecordWorkerHeartbeat` `PermissionDenied: Request
  unauthorized` WARN — the write-scoped worker key isn't authorized for the newer worker-
  heartbeat RPC. Workflows execute fine (polling 10/10, order completed); pre-existing, not
  caused by the rename. Worth a follow-up if worker-management heartbeat telemetry is wanted.

## Next / follow-ups

- Execute the destructive sequence on go-ahead; then commit.
- Domain axis (`payments`) → Nexus; region axis (per-domain `regions`) → multi-region HA, when
  wanted. These are the real multi-namespace Temporal exhibits.
