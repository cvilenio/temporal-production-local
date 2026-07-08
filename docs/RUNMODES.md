# Run modes — local/cloud × the backend matrix

The application stack is **backend-agnostic**: orders-service and the workers read their
Temporal connection from env (`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TLS`,
`TEMPORAL_API_KEY`) and never hardcode where Temporal lives. "Which backend" is injected
config, not a code or topology change. That one contract spans every run mode below and
carries forward to kind/Helm (a k8s Secret) unchanged.

## The matrix

Two axes. **Where the apps run** (local laptop) × **where Temporal lives** (the backend).

| Apps/workers run on | Backend          | How                                              | Status |
|---------------------|------------------|--------------------------------------------------|--------|
| **kind**            | **Temporal Cloud** | `just host-plane-up-cloud` + `just platform-up` (ArgoCD + Cloud API-key Secrets; kind runs workers + app tier) | ✅ **the supported path (default)** |
| **kind**            | **Local OSS server** | `just host-plane-up-oss` + `just platform-up oss` (ArgoCD + `charts/temporal-server`: official Temporal chart + CNPG Postgres + cert-manager frontend mTLS + bootstrap Job) | ✅ **supported** (ADR-0003) |
| Compose             | Local OSS server | `just legacy-up` (server + app tier, **no workers**)     | ⚠️ legacy fallback — see below |

**The pivot (this is the important part).** Temporal **workers run on kind** (Worker
Deployment), not in Compose. Compose's role is now narrowed to two things: (1) the host
visibility/console plane for the kind paths (`host-plane-up-cloud`), and (2) a **legacy local
self-hosted OSS server + app tier** fallback (`legacy-up`). Running workers, or the full app
tier against Cloud, on Compose is **no longer a goal** — those modes (`up-cloud`,
`up-cloud-prod`, `compose/workers.yml`) have been removed.

The legacy `just legacy-up` brings up a local Temporal **server + app tier with no workers**, so
workflows don't *execute* there until OSS-on-kind lands; it's useful for SDK/server
poking and as the place metrics were historically exercised, not an end-to-end demo. The
supported end-to-end path is **kind + Cloud**.

## How backend selection works (and the direnv footgun)

Compose interpolates `${TEMPORAL_ADDRESS:-…}` from the **shell environment first**, then
any `--env-file`. Because `.envrc`/direnv exports `TEMPORAL_*` for host-direct SDK runs
(`localhost:7233`), relying on `--env-file` alone is unsafe — the shell value wins and
breaks both Cloud mode and in-container OSS mode.

So each `just` recipe **sources its connection profile into the compose process**, making the
backend deterministic regardless of the host shell:

| Task              | Sources                          | Compose files                              |
|-------------------|----------------------------------|--------------------------------------------|
| `host-plane-up-cloud`   | `.secrets/keys/cloud.env`        | base only (kind runs the workers AND the app tier) |
| `legacy-up` / `legacy-fresh` | `config/local-oss.env`      | base + `host-apptier.yml` + `oss-server.yml` (no workers) |
| `legacy-down` / `host-plane-down` | —                      | (matching set; `-v` drops volumes)         |

`legacy-down` must use the same `-f` set as its `legacy-up`. **Bring the stack down before switching
modes** (they share host ports and one Compose project).

On the **kind path, the cluster runs both the workers AND the app tier** (orders-db via
CloudNativePG + orders-service), so `host-plane-up-cloud` is base-only: no `host-apptier.yml`.
The still-on-host console + pgweb reach the in-cluster app tier through the host ports kind
maps — `host.docker.internal:8002` (orders-service) and `:5433` (orders-db). The sourced
Cloud profile also gives the console its read-only Temporal Cloud liveness credentials.

## Files

- **`docker-compose.yml`** — base: the host visibility/console plane (console, observability,
  mock-api, **pgweb**, kind cluster observers). NOT the workers, NOT the app tier. No Temporal
  backend; `TEMPORAL_*` default to empty/local. Console + pgweb default to the kind-mapped host
  ports; the host-apptier overlay overrides them for the local-OSS mode. Carries the two
  injected descriptors — `CONSOLE_SUBSTRATE` (compose|kind) and `CONSOLE_BACKEND` (cloud|oss).
- **`compose/host-apptier.yml`** — the app *tier* layer: orders-db (Postgres) + orders-service.
  Included on the legacy local-OSS path; omitted on the kind path (kind runs orders-db via CNPG
  + orders-service via the `orders-app` chart). Repoints the console + pgweb at the in-compose
  service names.
- **`compose/oss-server.yml`** — the OSS backend *layer*: Temporal server + its Postgres +
  schema/namespace/search-attribute bootstrap + Web UI; re-attaches orders-service's
  `depends_on: temporal` and sets `CONSOLE_BACKEND=oss`. Omit it to run against Cloud. (There is
  no worker layer — workers run on kind.)
- **`config/temporal/namespaces.yaml`** — shared namespace/search-attr/retention spec; the
  single source of truth read by both Cloud (Terraform) and OSS (the bootstrap renderer).
- **`config/local-oss.env`** — local-OSS connection profile (tracked; no secrets).
- **`.secrets/keys/cloud.env`** — the Cloud connection profile (git-ignored; holds the
  worker API key + endpoint + the read-only observer key). Generated from
  `deploy/terraform/layers/cloud` outputs, keyed by `<domain>` (no env axis). A second
  domain's workers on kind would get their own `cloud-<domain>.env`.

## Topology vs. backend vs. add-ons

- **Topology / backend** → override files (`-f`). Server present or not.
- **Optional add-ons** → Compose profiles (future: tag codec-server / extra tooling).
  Don't put the server dependency behind a profile — `depends_on` would drag it back in.

## Cloud namespaces and multiple business cases

Cloud namespaces are provisioned by `deploy/terraform/layers/cloud`, keyed by **domain**
so business domains coexist on the one account (`<account-id>`). There is **no
nonprod/prod env axis** (ADR-0017) — the repo models one production-shaped environment:

```
ziggymart       # retail / orders (today)
payments        # a future domain — just add a spec entry + overlay entry
```

Convention: `<domain>`. Each domain gets its own namespace + least-privilege worker
(and optional client) service account + API key — which is what enables **Nexus** across
domains and **per-domain auth**. A new business case = add it to the shared spec (below) +
add its Cloud overlay entry + its own app stack (its own `TEMPORAL_NAMESPACE` profile); the
orders app is simply the first domain. The other axes that matter: **region** (per-domain
`regions` in the overlay → multi-region HA), not environment.

## One spec, no Cloud↔OSS drift

Namespace identity, custom **search attributes**, and retention live once in
`config/temporal/namespaces.yaml` — the single source of truth both backends read (ADR-0007):

- **Cloud:** `deploy/terraform/layers/cloud` reads the spec via `yamldecode()` and merges a
  Cloud-only overlay (`cloud_overlay`: service account, API key, regions) on top.
- **OSS (Compose):** `just legacy-up` runs `compose/scripts/render-oss-bootstrap.py` to render the
  spec to a shell file the `temporal-search-attributes` container loops over.

The Compose bootstrap containers are a **non-prod local convenience**. The production-grade
equivalent on kind is an **Argo-managed Job rendered from the same spec** (ADR-0007), with
OSS auth (mTLS + JWT) as a follow-on (ADR-0008). Edit a search attribute once in the spec and
it surfaces in both the Cloud `terraform plan` and the next OSS `just legacy-up`.

## When kind replaces Compose-OSS

kind is a *local flavor*, not a new contract. The Helm worker chart already reads the same
`TEMPORAL_*` (`deploy/charts/orders-workers`); the backend is selected by a k8s Secret
(Cloud) or the in-cluster `charts/temporal-server` (local OSS) — exactly the Compose backend
split, one level up. Once OSS-on-kind lands, the kind cluster will run the workers against
*either* backend, and the legacy Compose-OSS server+app fallback can retire entirely.

### kind + Cloud — how to run it

**Console first (required).** The `platform-console` (:8086) is the operator's live window;
bring it up before any live kind testing so a human can follow along. `just host-plane-up-cloud`
then `just headlamp-reload`. This is enforced — `just platform-up` and `just orders-db-reset`
run `just preflight` (probes `:8086/healthz`) and abort if the console is down. See AGENTS.md
("Live kind testing — bring the platform-console up FIRST").

```sh
just host-plane-up-cloud  # host visibility + console + mock-api (start this FIRST; console is the live window)
just headlamp-reload

just platform-up    # cluster + local registry, mirror deps, CI (build/push), publish chart,
                    # pin workers by digest, terraform apply. One command, each step idempotent.
                    # Gated on `just preflight` — fails fast if the console isn't up.

# or step by step:
just cluster-up                                     # kind + local registry (kubeconfig -> .secrets/kube)
just mirror-deps                                    # cert-manager + worker-controller charts+images -> local registry
just ci                                             # lint + test + build/push worker images
just chart-publish                                  # publish orders-workers + orders-app charts to the local OCI registry
terraform -chdir=deploy/terraform/layers/cluster init && \
terraform -chdir=deploy/terraform/layers/cluster apply   # pass TF_VAR_worker_image_digests to pin by digest

# ArgoCD UI: http://localhost:8090 (kind NodePort) — framed in the demo console at
# http://localhost:8088 (via viz-proxy). Cluster explorer (Headlamp): console :8087. (ADR-0014)
```

Things worth knowing:

- **kind lifecycle is CLI-owned** (`deploy/kind/cluster-up.sh`, via `just`), not the Terraform
  kind provider. Terraform's kubernetes/helm providers read the written kubeconfig, so they never
  depend on a not-yet-created resource. Mirrors pointing at a pre-existing GKE cluster (ADR-0009).
- **Delivery is local-only** (ADR-0011): the local registry is **zot**, which hosts our pushes
  **and pull-through-caches** upstream images on demand. ArgoCD pulls **every** chart from it
  (third-party charts pushed by `just mirror-deps`, orders-workers by `just chart-publish`); all
  Applications are seeded by Terraform. Container images are fetched-and-cached by zot on first node
  pull — no enumeration, new tags self-populate. **No GitHub/public-internet dependency** for
  steady-state delivery. Your `git push origin main` is unchanged; ArgoCD just doesn't read git, so
  local chart/app edits reconcile after `just chart-publish` / re-apply — not after a git push.
- **API keys require the *regional* endpoint** (`<region>.<cloud>.api.temporal.io:7233`), not the
  namespace endpoint (`<ns>.<acct>.tmprl.cloud:7233`) — the latter rejects API keys with
  `tls: certificate required`. Our Cloud namespaces are `api_key_auth=true`, and the cloud layer's
  `endpoint` output already returns the regional form, so the cluster layer wires it through
  automatically. TLS stays on with API keys.
- **Workers are pinned by image digest** (ADR-0012): the git-describe tag is for humans, the
  `sha256` digest is the deploy contract, so the Worker Controller Build ID is content-addressed.
- **The console's architecture page is live on kind** (ADR-0015 phase-2+): it reads cluster pod
  state via a **read-only** ServiceAccount kubeconfig (`console-reader`) that `cluster-up.sh` mints
  into `.secrets/kube/kind.console.kubeconfig`. Two injected descriptors drive it:
  `CONSOLE_SUBSTRATE` (base defaults `kind`; the host-apptier overlay sets `compose`) and
  `CONSOLE_BACKEND` (base defaults `cloud`; `oss-server.yml` sets `oss`). On kind+Cloud the live
  status is a union: cluster pods from Kube (orders-*, orders-db, **argocd**), host-plane tooling
  (lgtm, console, mock-api, pgweb, viz-proxy, headlamp, codec-server) from Docker, and a
  **console-owned Temporal Cloud liveness probe** — namespace reachability (a read-only SDK
  `DescribeNamespace`; Cloud API keys are not authorized for the gRPC health service) plus the
  public Temporal Statuspage. With the optional read-only **observer key**
  (`TEMPORAL_CLOUD_OPS_API_KEY`, the cloud layer's `observer_api_key_token`), the console also
  calls the Cloud Ops API (`GetNamespaces`/`GetRegions`) to render a **regions + namespaces**
  status block (account-scoped `read` + per-namespace read; see `observer.tf`). The Tooling strip is data-driven, so it
  adapts: OSS-only nodes (in-Compose Temporal sim, ui-proxy, pgweb-temporal) show only on the
  `oss` backend; cluster-visibility tooling only on the `kind` substrate.
- **Reset Demo State is backend-aware** (ADR-0015): on the `cloud` backend it is scoped to local
  business data only (truncate `orders`/`idempotency_keys` + clear the submission log) and never
  terminates or deletes workflows in the managed Cloud namespace; on `oss` it keeps the full
  reset (terminate in-flight + optionally delete closed history).

The account-bearing namespace handle + API keys are read from the cloud layer's state by the cluster
layer and injected cluster-side (Secrets + ArgoCD Application valuesObject) — never committed
(`.githooks/pre-commit`).

### kind + OSS server — how to run it (ADR-0003)

The self-hosted backend runs the official Temporal chart (`deploy/charts/temporal-server`, a wrapper
over `go.temporal.io/helm-charts`) with CNPG Postgres, **frontend mTLS** via cert-manager (ADR-0008),
and an Argo-managed bootstrap Job that registers the namespace + search attributes from the shared
`config/temporal/namespaces.yaml` (ADR-0007). `numHistoryShards` is **512** (Temporal's small-prod
standard) and is a tunable chart value — immutable in-place, so `just temporal-db-reset` is the local
re-pick escape hatch.

```sh
just host-plane-up-oss                 # host visibility + console (CONSOLE_BACKEND=oss) — start FIRST
just platform-up oss     # same bring-up as Cloud, but temporal_backend=oss + the OSS server
```

The connection contract is unchanged — `tls` stays **on**; only the credential type differs (Cloud
API key ↔ OSS mTLS client cert issued by cert-manager into the `orders` namespace). The console
sidebar Temporal link + `/architecture` work on OSS: the Web UI is fronted by the viz-proxy
(`http://localhost:8089`, frame-stripped) and the server pods surface via the console's kube status.

#### Switching a live backend (the guarded, deliberate way)

**Do not flip `temporal_backend` by hand on a running stack.** Use the single control point:

```sh
just switch-backend oss     # or: cloud
just switch-backend cloud --drain   # wait for in-flight workflows first
just switch-backend cloud --yes     # skip the prompt (automation)
```

`switch-backend` is a **hard switch** (no shadowing / Cloud↔OSS replication). It detects open
workflows on the current backend and **prompts y/n** before an orphaning switch, repoints the
workers/apps (`terraform apply`, reusing the current image digests — no worker rebuild), then
recreates the host console with the target profile. The directions are asymmetric:

- **Cloud → OSS:** Cloud workflows are preserved by Cloud (idle until you switch back).
- **OSS → Cloud:** OSS workflows stay in the local Postgres; the OSS server keeps running
  (state preserved) because its lifecycle is **decoupled** from the toggle.

Destroying the OSS server is a separate, explicit step: `just temporal-server-down` (refuses while
the backend is still `oss`). Cloud remains the default backend.

### App tier on kind (orders-api + orders-db)

On the kind path the **app tier runs in-cluster too**, co-located with the workers in the `orders`
namespace (`deploy/charts/orders-app`):

- **orders-api** — the Temporal client (starts/signals workflows) + system of record. Authenticated
  to Cloud as a **dedicated client service account + API key**, distinct from the worker identity
  (ADR-0008): the client needs `write` to start workflows, and a separate credential keeps its blast
  radius independent of the worker fleet. Exposed to the host as a NodePort (`:8002`) for the console
  and the order E2E.
- **orders-db** — PostgreSQL run by the **CloudNativePG (CNPG) operator** (`deploy/charts/cloudnative-pg`
  add-on, sync-wave −2): a primary + replica with auto-failover and the standard `-rw`/`-ro` Services.
  A NodePort (`:5433`) exposes the primary so the host console's direct asyncpg pool can read it.
- **mock-api stays on the host** as the simulated external dependency; workers reach it via
  `host.docker.internal:8001` (`MOCK_API_URL`), which correctly models cluster egress.

#### orders-db state lifecycle (when state survives, how to clear it)

orders-db uses kind's default `local-path` PVC — data lives **in the kind node container**, not on
your host disk. Two reset paths, do not confuse them:

| Action | orders-db state |
|---|---|
| Pod restart · `selfHeal` · image rebuild · redeploy | **survives** (PVC outlives pods) |
| `just cluster-stop` → `just cluster-start` | **survives** (node container preserved) |
| `just cluster-down` (`kind delete cluster`) | **gone** — node container destroyed with the PVC |
| `just orders-db-reset` | **gone** — *physical* reset: deletes the CNPG Cluster + PVCs; ArgoCD re-syncs an empty DB |
| Console "Reset demo" / `POST /admin/reset` on orders-api | kept — *logical* reset. On Cloud: local data only (truncates app tables, clears the submission log); never touches the managed namespace. On OSS: also terminates in-flight + optionally deletes closed history. DB lives. |

To persist across a full `cluster-down`, you'd bind the PVC to a host dir via kind `extraMounts`
(deliberately not done — it's a Compose bind-mount habit, less production-faithful than letting the
operator own storage).

## Offline contract — what's air-gapped vs not (ADR-0013)

**Principle: the air-gap boundary is the OCI registry. We cache artifacts (images + charts), not
source indexes (PyPI/npm/Go/Maven/…).** Anything that runs *in the cluster* is an OCI artifact
served locally by zot; anything *upstream of producing* those artifacts may need the network.

| Tier | Activity | Offline? |
|---|---|---|
| **Run** | deploy + execute workloads on a warm cluster | ✅ guaranteed (fully local) |
| **Rebuild** | rebuild an image from already-resolved deps | ⚠️ best-effort (uv cache only) |
| **Resolve** | pull new/changed source deps, base images, tools | ❌ requires network (by design) |

In one line: **a warmed cache runs and re-runs the platform offline; producing new artifacts
requires connectivity** — go offline *after* a warm build, not before. We do **not** mirror PyPI/
npm/Go/Maven/crates or build-time base images; a customer needing truly-offline builds proxies all
package types through Artifactory/Harbor (their platform, not this repo). zot's first pull-through
and ArgoCD's own bootstrap are tier-3 "warm it once," consistent with the contract.

### Going offline (e.g. on a plane): stop, don't delete

The key is **stop ≠ delete**. A stopped cluster restarts fully offline (node containerd image cache
+ zot's `artifact-registry-data` volume both persist); a *deleted* cluster can't be recreated offline
(`kindest/node`, the argo-cd chart, and ArgoCD's images are tier-3 bootstrap — see ADR-0013).

```sh
# on the ground, online — warm everything once:
just platform-up
just k get applications -n argocd     # confirm Synced/Healthy
just cluster-stop                     # docker stop nodes + registry; state preserved

# on the flight, offline:
just cluster-start                    # docker start; workers + apps resume, zero network
```

`cluster-down` (delete) is for reclaiming resources, not for going offline — use `cluster-stop`.
