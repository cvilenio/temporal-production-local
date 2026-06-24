# 0004 — Temporal Cloud base layer + swappable run modes

- **Status:** **LANDED (2026-06-23, committed `55b2e60`).** Two Cloud namespaces live;
  `happy_path` order verified end-to-end against Cloud nonprod; Compose split into
  backend-agnostic base + OSS layer. Account id redacted from all git history (force-pushed).
- **Date:** 2026-06-23

## Done this session

### Temporal Cloud, provisioned by Terraform (layered)
- New layered Terraform under `deploy/terraform/`: `modules/cloud-namespace/` (reusable
  per-namespace block) + `layers/cloud/` (base layer). Independent of kind — `init` pulls
  only `temporalio/temporalcloud` (resolved 0.9.2; pinned `~> 0.9`). Legacy `main.tf`
  (kind+ArgoCD) left as the seed for `layers/cluster` (stub READMEs added). Deleted the old
  `cloud.tf` skeleton.
- `terraform apply` created, on account `<account-id>`: namespaces **`ziggymart-nonprod`** (retention
  14) and **`ziggymart-prod`** (retention 30), each with `api_key_auth=true`, a least-privilege
  worker service account (account `read` + namespace `write`), a worker API key, and the
  orders custom **search attributes** (`OrderId`/`TraceId`/`OrderStatus`) via
  `temporalcloud_namespace_search_attribute` — declarative namespace setup, not out-of-band.
- Namespace map keyed by **full namespace name** so future business domains coexist
  (add `payments-nonprod`, etc.). `prevent_destroy` on every namespace.
- **Endpoint gotcha:** API-key auth uses the **regional** endpoint
  `us-east-1.aws.api.temporal.io:7233` (from the provider's computed `endpoints.grpc_address`),
  not the `<ns>.<acct>.tmprl.cloud` mTLS form. `TEMPORAL_NAMESPACE` is the `<ns>.<account-id>` handle.

### Secrets layout
- Renamed `.keys/` → **`.secrets/`** (`chmod 700`), subdirs `keys/` (bootstrap key +
  generated `cloud-{nonprod,prod}.env` profiles) and `terraform/` (local tfstate). gitignore
  tracks only the README + `.gitkeep`s; `.dockerignore` updated. Cloud-layer local backend
  writes `.secrets/terraform/cloud.tfstate`. State recovery = `terraform import` (Cloud is
  source of truth) + rotate the key (secret not recoverable); see `layers/cloud/README.md`.
- **Account id** lives only in `.secrets/account.env` (git-ignored): `TF_VAR_account_id`
  feeds `var.account_id` (no committed default). Source it before any terraform command in
  `layers/cloud`. Tracked files/docs use the `<account-id>` placeholder.

### Security / git hygiene
- The real account id (`<account-id>`) had leaked into committed history (pre-existing files +
  this session's drafts) and was **already pushed**. Redacted across all 25 commits with
  `git filter-repo --replace-text` (→ `<account-id>`) and **force-pushed** `main`. Verified:
  0 occurrences in any local/remote ref or reflog; old objects gc'd. Repo is PRIVATE, 0 forks.
  Residual: GitHub may retain unreachable old SHAs until it GCs (low risk; ask Support to
  expedite if needed). It's an account *identifier*, not a credential — keys were never committed.
- Pre-commit guard (`.githooks/pre-commit`) taught to ignore obvious placeholder/redaction
  tokens (`<account-id>`, `REDACTED`, `example.com`, …) so it stops flagging its own redactions.

### Compose: backend-agnostic base + OSS layer + poe
- `docker-compose.yml` is now apps/workers/observability only; `TEMPORAL_*` via
  `${VAR:-default}`, no Temporal server, no hard `depends_on: temporal`.
- `compose/oss-server.yml` = the OSS backend layer (server + its Postgres + namespace/
  search-attribute bootstrap + Web UI) and re-attaches the apps' `depends_on: temporal`.
- `poe` tasks `up` (OSS), `up-cloud`, `up-cloud-prod`, `down`/`down-cloud`/`fresh`/`fresh-cloud`
  — each **sources its connection profile into the compose process** (the `direnv` footgun:
  compose interpolation takes shell env over `--env-file`, so relying on `--env-file` alone
  breaks both Cloud and in-container OSS). Profiles: `config/local-oss.env` (tracked) +
  `.secrets/keys/cloud-*.env`.
- `docs/RUNMODES.md` documents the 2×2 matrix (Compose|kind × OSS|Cloud), the footgun, and
  multi-namespace.

### Verified live
- Cloud-nonprod stack built + came up (no local server). Workers connected to
  `ziggymart-nonprod.<account-id>`. Submitted `happy_path` → an order reached
  `completed`; `OrderWorkflow` confirmed **COMPLETED** in the namespace via `list_workflows`.
- `terraform validate`/`fmt` clean; post-import `plan` = **No changes**.

## Decisions
- **Auth:** API key only this phase (no mTLS), per user. App code already supported it.
- **State:** single unified Cloud-layer state, `for_each`; local backend in `.secrets/`; no
  remote/object-store backend by design (no cloud beyond Temporal Cloud).
- **Key:** Terraform mints the worker key (in state); `create_api_key` toggle leaves the
  out-of-band `tcld` path open. Bootstrap key = a dedicated account-admin service account.
- **Run modes:** override files for topology/backend, profiles reserved for optional add-ons;
  single-trunk + tag-pinning for prod (documented, not yet built).
- These should be promoted to ADRs (none written yet this session).

## Open questions
- Bootstrap key storage: `source`-from-file is the current baseline; move to Keychain/1Password
  and/or disable the key between applies? (security discussion held, not actioned.)
- Search attributes registered on **prod** too, but prod has no app stack yet — fine.

## Next
- Quick `poe up` OSS smoke test (only `docker compose config` was validated this session).
- Tidy cosmetic residual: the history redaction mangled a `.gitignore` glob into
  `*.<account-id>.txt` (a dead literal glob); real cred files live under the fully-ignored
  `.secrets/`. Drop or fix that line.
- Build `layers/cluster` (kind + ArgoCD + Cloud-API-key k8s Secret) — the local-kind flavor.
- Worker chart `connection.yaml` needs an API-key/env-injection branch for Cloud on kind.
- Promote this session's decisions to ADRs.

Compose stack already brought down (`down -v`); cloud namespaces persist (Terraform state).
