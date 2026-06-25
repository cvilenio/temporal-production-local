# 0011 — app tier onto kind (orders-api + orders-db via CNPG), dedicated client identity

- **Status:** **LANDED + LIVE-VALIDATED (2026-06-25).** Order completes E2E on kind+Cloud
  (the 0010 blocker is resolved). Two bugs surfaced during the live run and were fixed (see
  "Live-run findings"); both orders charts bumped to **0.1.1**. Not yet committed.
- **Date:** 2026-06-25
- **ADRs:** none new. Updated **ADR-0003** (app datastore orders-db also on CloudNativePG —
  one Postgres story) and **ADR-0008** (data plane splits into worker vs **client** identity;
  control / worker / client = three least-privilege credentials).

## Why

0010 left one decided-but-unbuilt finding: the order's activity failed with
`All connection attempts failed` calling `orders-service` because kind workers couldn't reach the
app tier still living on the Docker host (half-state). This checkpoint moves the **app tier onto
kind** so an order completes E2E: in-cluster orders-api starts the workflow on Cloud, kind workers
execute it, the activity reaches the host mock-api via `host.docker.internal`, state persists to an
in-cluster orders-db.

## Done this session

- **orders-api image (`pyproject.toml`).** `build-images`/`push-images`/`image-digests` now also
  build+push `localhost:5001/orders-api:<git-sha>` (uvicorn `main:app`), alongside the two worker
  images. `ci` chains it.
- **CNPG operator add-on.** `config/dependencies.yaml` + `render-deps.py` + `mirror-deps.sh` add
  **cloudnative-pg 0.28.3** (operator appVersion 1.29.1; classic Helm repo
  `https://cloudnative-pg.io/charts`, mirrored like cert-manager). New
  `deploy/argocd/applications/cloudnative-pg.yaml` — sync-wave −2, `prune:false`,
  `ServerSideApply`, ns `cnpg-system`. Auto-seeded + version-injected by the cluster TF.
- **New chart `deploy/charts/orders-app`.** CNPG `Cluster` orders-db (1 primary + 1 replica,
  bootstrap adopts a pre-created basic-auth Secret so the password is **known** — local-dev
  `admin`/`password`/`orders_db`, matching Compose, so the host console's direct asyncpg pool
  works; operator-managed NodePort exposes the rw primary). orders-api Deployment (hardened KSA,
  token off; DB password via `secretKeyRef` → `DATABASE_URL` built with `$(DB_PASSWORD)`; Cloud
  `TEMPORAL_*` from the client apikey Secret). Service **named `orders-service`** (NodePort) so the
  workers' default `ORDERS_SERVICE_URL=http://orders-service:8000` resolves in-cluster AND the host
  reaches orders-api via the mapped port. Resource-level sync-waves: DB (−1) before orders-api (0).
- **Dedicated client SA + key (Terraform).** `cloud-namespace` module mints an optional second SA
  + API key (`client_service_account_name`, write perm) with its own outputs. Cloud-layer overlay +
  aggregated `client_api_key_tokens` output; `terraform.tfvars(.example)` set `orders-api-nonprod`
  for ziggymart-nonprod. Cluster layer reads it from remote state and seeds the
  **`orders-client-apikey`** Secret in the `orders` ns.
- **orders-app ArgoCD Application (TF-seeded).** `applications.tf` `orders_app_application`
  (sync-wave 0) injects the orders-api image (digest-or-tag, same footgun precondition) + client
  Secret + Cloud connection. `chart-publish` now packages both orders charts; `platform-up` exports
  the orders-api digest as `TF_VAR_orders_api_image_digest`.
- **Host↔kind reachability.** orders-workers chart injects
  `MOCK_API_URL=http://host.docker.internal:8001` (always-present env; `TEMPORAL_TLS` stays
  conditional). `kind-config.yaml` adds NodePort→host maps reusing the freed Compose ports:
  orders-api `30800→8002`, orders-db `30543→5433`; documented the `host.docker.internal` resolution
  expectation + CoreDNS fallback.
- **Compose plane split (`up-cloud-kind` trim).** New `compose/host-apptier.yml` holds orders-db +
  orders-service + pgweb + the `orders-db-data` volume, and repoints the console at the in-compose
  names. Base `docker-compose.yml` is now console + mock-api + visibility only; the console defaults
  point at the kind-mapped host ports (`host.docker.internal:8002/:5433`) with a host-gateway
  `extra_hosts` + `restart: unless-stopped`. poe `up`/`up-cloud`/`up-cloud-prod`/`down*` include the
  overlay; **`up-cloud-kind` is base-only** (kind runs BOTH workers and app tier).
- **State ergonomics.** `just orders-db-reset` (confirm-gated) deletes the CNPG Cluster + PVCs →
  ArgoCD re-syncs an empty DB. RUNMODES gained a **state-lifecycle table** (what survives pod
  restart / cluster-stop vs cluster-down / reset) and the physical-vs-logical-reset distinction.

## Verification

- **Static (all green):** `helm lint` both charts; **kubeconform** orders-app **5/5** (KSA + Secret +
  CNPG `Cluster` + Deployment + Service), orders-workers 5/5, plain manifests **6/6** (incl. the new
  cloudnative-pg Application), `-strict`. Full `uv run poe lint` green (ruff/format/pyright 0 errors).
  `terraform fmt` clean + `validate` Success on both cloud and cluster layers. `docker compose
  config -q` valid for all `-f` combos; base-only path has **0 app-tier services**, overlay path adds
  them; console DB/orders URLs flip correctly (host.docker.internal ↔ orders-db).
- **Live (DONE, 2026-06-25):** cloud apply added the `orders-api-nonprod` SA + key (2 add, 0
  change/destroy — no namespace churn). `just platform-up` brought up the cluster; all 6 ArgoCD
  apps Synced/Healthy; CNPG operator Healthy; orders-db primary+replica Healthy; orders-api Ready
  under its KSA; `orders-client-apikey` Secret present. `host.docker.internal:8001` **resolves and
  reaches host mock-api** from kind pods (no CoreDNS fallback needed on Docker Desktop). **Order
  E2E:** `ORD-F3TFGDF60N9ASX7R` ran `pending → shipment_created → completed` in ~8s — saga fully
  populated (reservation/tracking/capture IDs), proving in-cluster orders-api → kind workers →
  in-cluster orders-service + host mock-api → in-cluster orders-db (CNPG).

## Live-run findings (both bugs, both fixed this session)

1. **orders-app Secret applied AFTER the CNPG Cluster → ArgoCD deadlock.** The `orders-db-app`
   credential Secret had no sync-wave (default 0) while the Cluster was wave −1, so CNPG initdb
   failed `secret "orders-db-app" not found`, the Cluster never went Healthy, and the sync
   operation **hung at wave −1 forever** — a hung op is not a failed op, so selfHeal/hard-refresh
   never re-ran it (even after the chart was corrected). Unblocked live by applying the Secret
   manually + deleting the wedged Cluster. **Fix:** Secret → sync-wave **−2** (before the Cluster).
   Hardening follow-up under consideration: seed `orders-db-app` via Terraform (like the apikey
   Secrets) so it leaves the GitOps wave graph entirely — removes the deadlock class, not just this
   instance.
2. **Workers missing `ORDERS_SERVICE_URL` → activities dialed localhost.** The 0010 note assumed
   the kernel's default `orders_service_url` was the in-cluster `http://orders-service:8000`; it is
   actually the host-dev `http://localhost:8002`. Compose set it explicitly; the kind workers did
   not, so every activity (incl. the `mark_order_failed` saga compensation) failed "All connection
   attempts failed" and the workflow Failed. **Fix:** orders-workers chart now injects
   `ORDERS_SERVICE_URL=http://orders-service:8000` (alongside MOCK_API_URL).

Both fixes bumped the charts to **0.1.1** (and the cluster-layer `orders_{workers,app}_chart_version`
defaults). The wave −2 fix is published but was masked live by the manual unblock — it should be
re-confirmed on a from-scratch bring-up (fresh cluster) before treating the cold-start path as
proven.

## Known accepted gap (→ 0012)

The console still uses `docker_status` (Docker socket), which can't see kind pods — so the
**architecture page paints the app-tier nodes DOWN** until 0012. The console's orders/tracking pages
keep working (they hit the repointed host ports). 0012 = ADR-0015 phase-2 `kube_status` provider
(refactor docker_status into a `StatusProvider` interface; add a kubeconfig-backed provider; same
swap for log streaming) — closes this gap.

### Carried over (still open)

- ADR-0015 phase 3: topology-as-data (multi-domain).
- Wire observability onto kind (chart + scrape) — orders-api/workers currently emit OTel best-effort
  (kind obs not wired; exporter tolerates an unreachable collector).
- `kind + Local OSS` run-mode row (in-cluster `charts/temporal-server` backend).
