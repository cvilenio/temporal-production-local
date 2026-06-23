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
| kind        | Temporal Cloud     | ArgoCD + Cloud API-key Secret                    | planned |

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
service account + API key. A new business case = new keys in the `namespaces` map + its own
app stack (its own `TEMPORAL_NAMESPACE` profile); the orders app is simply the first domain.

Custom **search attributes** per namespace are part of namespace setup: declared in the
cloud layer (`search_attributes` map → `temporalcloud_namespace_search_attribute`) and
applied by Terraform. OSS does the equivalent automatically via the
temporal-search-attributes bootstrap container.

## When kind replaces Compose-OSS

kind is a *local flavor*, not a new contract. The Helm worker chart already reads the same
`TEMPORAL_*` (`deploy/charts/orders-workers`); the backend is selected by a k8s Secret
(Cloud) or the in-cluster `charts/temporal-server` (local OSS) — exactly the Compose split,
one level up. The cluster layer (`deploy/terraform/layers/cluster`) creates the cluster,
installs ArgoCD, and lands the Cloud API-key Secret. Compose-OSS stays as the fast fallback.
