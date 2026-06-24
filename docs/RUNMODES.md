# Run modes — local/cloud × the backend matrix

The application stack is **backend-agnostic**: orders-service and the workers read their
Temporal connection from env (`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TLS`,
`TEMPORAL_API_KEY`) and never hardcode where Temporal lives. "Which backend" is injected
config, not a code or topology change. That one contract spans every run mode below and
carries forward to kind/Helm (a k8s Secret) unchanged.

## The matrix

Two axes. **Where the apps run** (local laptop) × **where Temporal lives** (the backend).

| Apps run on | Backend            | How                                              | Status |
|-------------|--------------------|--------------------------------------------------|--------|
| Compose     | Local OSS server   | `poe up`                                         | ✅ working |
| Compose     | Temporal Cloud     | `poe up-cloud` / `poe up-cloud-prod`             | ✅ working |
| kind        | Local OSS server   | ArgoCD + `charts/temporal-server` (cluster layer)| planned |
| kind        | Temporal Cloud     | `just cluster-up` → `layers/cluster` apply (ArgoCD + Cloud API-key Secret) | ✅ working |

The user-facing framing: **two local flavors** (Compose, kind) and **two Cloud flavors**
(nonprod, prod). Compose is the fast laptop path and stays a first-class fallback even
after kind can run OSS.

## How backend selection works (and the direnv footgun)

Compose interpolates `${TEMPORAL_ADDRESS:-…}` from the **shell environment first**, then
any `--env-file`. Because `.envrc`/direnv exports `TEMPORAL_*` for host-direct SDK runs
(`localhost:7233`), relying on `--env-file` alone is unsafe — the shell value wins and
breaks both Cloud mode and in-container OSS mode.

So each `poe` task **sources its connection profile into the compose process**, making the
backend deterministic regardless of the host shell:

| Task              | Sources                          | Compose files                              |
|-------------------|----------------------------------|--------------------------------------------|
| `up` / `fresh`    | `config/local-oss.env`           | `docker-compose.yml` + `compose/oss-server.yml` |
| `up-cloud`        | `.secrets/keys/cloud-nonprod.env`| `docker-compose.yml`                       |
| `up-cloud-prod`   | `.secrets/keys/cloud-prod.env`   | `docker-compose.yml`                       |
| `down` / `down-cloud` | —                            | (matching set; `-v` drops volumes)         |

`down` must use the same `-f` set as its `up`. **Bring the stack down before switching
modes** (they share host ports and one Compose project).

## Files

- **`docker-compose.yml`** — base: apps, workers, observability, orders-db. No Temporal
  backend; `TEMPORAL_*` default to the local OSS server.
- **`compose/oss-server.yml`** — the OSS backend *layer*: Temporal server + its Postgres +
  schema/namespace/search-attribute bootstrap + Web UI, and re-attaches the apps'
  `depends_on: temporal`. Omit it to run against Cloud.
- **`config/temporal/namespaces.yaml`** — shared namespace/search-attr/retention spec; the
  single source of truth read by both Cloud (Terraform) and OSS (the bootstrap renderer).
- **`config/local-oss.env`** — local-OSS connection profile (tracked; no secrets).
- **`.secrets/keys/cloud-{nonprod,prod}.env`** — Cloud profiles (git-ignored; hold the
  worker API key). Generated from `deploy/terraform/layers/cloud` outputs.

## Topology vs. backend vs. add-ons

- **Topology / backend** → override files (`-f`). Server present or not.
- **Optional add-ons** → Compose profiles (future: tag codec-server / extra tooling).
  Don't put the server dependency behind a profile — `depends_on` would drag it back in.

## Cloud namespaces and multiple business cases

Cloud namespaces are provisioned by `deploy/terraform/layers/cloud`, keyed by **full
namespace name** so business domains coexist on the one account (`<account-id>`):

```
ziggymart-nonprod   ziggymart-prod      # retail / orders (today)
payments-nonprod    payments-prod       # a future domain — just add map keys
```

Convention: `<domain>-<env>`. Each entry gets its own namespace + least-privilege worker
service account + API key. A new business case = add it to the shared spec (below) + add its
Cloud overlay entries + its own app stack (its own `TEMPORAL_NAMESPACE` profile); the orders
app is simply the first domain.

## One spec, no Cloud↔OSS drift

Namespace identity, custom **search attributes**, and per-env retention live once in
`config/temporal/namespaces.yaml` — the single source of truth both backends read (ADR-0007):

- **Cloud:** `deploy/terraform/layers/cloud` reads the spec via `yamldecode()` and merges a
  Cloud-only overlay (`cloud_overlay`: service account, API key, regions) on top.
- **OSS (Compose):** `poe up` runs `compose/scripts/render-oss-bootstrap.py` to render the
  spec to a shell file the `temporal-search-attributes` container loops over.

The Compose bootstrap containers are a **non-prod local convenience**. The production-grade
equivalent on kind is an **Argo-managed Job rendered from the same spec** (ADR-0007), with
OSS auth (mTLS + JWT) as a follow-on (ADR-0008). Edit a search attribute once in the spec and
it surfaces in both the Cloud `terraform plan` and the next OSS `poe up`.

## When kind replaces Compose-OSS

kind is a *local flavor*, not a new contract. The Helm worker chart already reads the same
`TEMPORAL_*` (`deploy/charts/orders-workers`); the backend is selected by a k8s Secret
(Cloud) or the in-cluster `charts/temporal-server` (local OSS) — exactly the Compose split,
one level up. Compose-OSS stays as the fast fallback.

### kind + Cloud — how to run it

```sh
just platform-up    # cluster + local registry, mirror deps, CI (build/push), publish chart,
                    # pin workers by digest, terraform apply. One command, each step idempotent.

# or step by step:
just cluster-up                                     # kind + local registry (kubeconfig -> .secrets/kube)
just mirror-deps                                    # cert-manager + worker-controller charts+images -> local registry
just ci                                             # lint + test + build/push worker images
just chart-publish                                  # publish orders-workers chart to the local OCI registry
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

The account-bearing namespace handle + API key are read from the cloud layer's state by the cluster
layer and injected cluster-side (Secret + ArgoCD Application valuesObject) — never committed
(`.githooks/pre-commit`).

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
+ zot's `kind-registry-data` volume both persist); a *deleted* cluster can't be recreated offline
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
