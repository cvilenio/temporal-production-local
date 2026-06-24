# 0008 — public release prep: README, teardown hardening, rename, first public push

- **Status:** **LANDED + PUSHED PUBLIC (2026-06-24).** Repo renamed and published at
  https://github.com/cvilenio/temporal-production-local (PUBLIC). Commit `1f8d0fd` on `main`,
  pushed with upstream tracking. Pre-push sensitivity scan: clean.
- **Date:** 2026-06-24
- **ADRs:** none new (no decisions promoted; this is a docs/ergonomics + release milestone).

## Why

Checkpoint 0007 left the platform live-verified (kind workers + Temporal Cloud, visibility
plane). Goal here: get the repo into a sensible, honest state and publish it publicly under
its new name. The old top-level README was the original Compose-only retail-demo writeup —
stale against the evolved kind + Cloud + GitOps platform — and the GitHub metadata
over-promised features that are scaffold/planned.

## Done this session

- **Top-level README rewrite.** Reframed around vision + an honest status board. The
  **kind workers + Temporal Cloud** path is the one supported, live-verified flow and gets a
  0-to-running getting-started (prereqs → `.secrets/` creds → cloud-layer `terraform apply`
  → `just platform-up` → derive Compose Cloud profile → `just up-cloud` → consoles). Compose,
  kind-OSS, app-tier-on-kind, polyglot, **observability-on-kind**, and the codec are marked
  **planned/scaffold**, not claimed working. Adopted the user's `temporal-production-local`
  intro (typo-fixed). Dropped the broken `via.placeholder.com` diagram.
- **Honesty calls grounded in code, not memory:** no observability chart on kind (only
  `orders-workers` + `temporal-server` charts); codec = scaffold (ADR-0006); polyglot =
  Python only; console nav now carries Headlamp/ArgoCD tabs that are inert in Compose.
- **`config/cloud.env.example` fix.** It pointed at an unused `config/cloud.env` and paired an
  API key with the *namespace* endpoint (rejected `tls: certificate required`). Now points at
  the `.secrets/keys/cloud-{nonprod,prod}.env` profiles the tooling actually sources, and uses
  the **regional** endpoint for API-key auth (mTLS branch keeps the namespace endpoint).
- **`just down` / `down-cloud` hardened.** They only tore down the pinned `COMPOSE_PROJECT_NAME=temporal`
  project, leaving stray default-project containers (a raw `docker compose up` →
  `<dir-basename>` project) and the shared `temporal-network` dangling. Now: `--remove-orphans`,
  a second sweep of the `${PWD##*/}` (basename) project, and an explicit `temporal-network rm`.
  `${PWD##*/}` keeps it **repo-name-agnostic**.
- **Rename `temporal-platform-demo` → `temporal-production-local`.** Only one tracked literal
  existed — `deploy/argocd/root-app.yaml` repoURL (a GitHub URL, can't be agnostic) → new name.
  Left untouched (not the repo name): kind cluster `temporal-platform`, `COMPOSE_PROJECT_NAME=temporal`.
- **Remote + push.** `origin` set-url to the new SSH remote; `git push -u origin main`.
- **GitHub metadata** aligned with the honest README (description no longer over-promises codec
  proxy / polyglot / observability) + 11 topics added.

## Verification

- **Getting-started validated against live warm state** (cluster from 0007 still up): prereq CLIs
  present; cloud layer `fmt`/`validate` clean + all outputs resolve; **regional endpoint claim
  confirmed** (`us-east-1.aws.api.temporal.io:7233`); `orders-workers` Synced/Healthy, worker pods
  Running; console/ArgoCD/Headlamp ports answered. Did NOT re-run `terraform apply` (real Cloud) or
  a fresh `platform-up` (warm state already present).
- **Teardown validated.** `down-cloud` + manual orphan removal + `cluster-down` left the host plane
  and kind clean with the **Cloud layer untouched** (state + namespaces intact, registry kept).
  Hardened `down`/`down-cloud` then re-tested with a deliberate two-project + shared-network setup:
  both projects' containers and `temporal-network` swept, exit 0, registry preserved.
- **Pre-push sensitivity scan (history + tree): clean.** No sensitive file ever tracked
  (`.env`/`.secrets/**`/tfstate/tfvars/`.pem`/`.key`/kubeconfig); account id `evvjb` never committed;
  no private-key/JWT/cloud-token patterns; no real key/token assignments; no PII/emails/public IPs;
  no stray `.terraform/` binaries. Only dev-default Postgres passwords (`temporal`/`password`,
  local containers) — whitelisted by the pre-commit hook, fine for public.

## Findings carried forward (not yet fixed)

- **Two worker fleets on the same Cloud namespace.** `just up-cloud` (step 5) also starts the
  Compose worker containers, which poll the *same* task queues as the kind workers — so it doesn't
  by itself prove the kind workers executed an order. README documents this as a known edge. The
  clean fix is a **Compose app-tier-only override** (workers excluded) so the kind fleet is sole
  executor. Deferred.
- **Stray compose-project drift** is a machine-state artifact (pre-`COMPOSE_PROJECT_NAME` containers),
  now swept by the hardened recipes rather than prevented at the source.

## Next

- Compose app-tier-only override (drop the duplicate worker fleet on the kind + Cloud path).
- ADR-0015 phase 2: `kube_status` provider → live architecture page on kind.
- ADR-0015 phase 3: topology-as-data for multi-domain.
- Wire observability onto kind (chart + scrape) — currently only proven on Compose-OSS.
- Move the app tier (orders-api, mock-api, console) onto kind; wire the in-cluster OSS
  `temporal-server` backend (the planned `kind + Local OSS` run-mode row).
