# ADR-0003: Temporal server backend — self-hosted on kind, Cloud-switchable

- **Status:** Accepted
- **Date:** 2026-06-23

## Context

The platform must replicate the full production lifecycle, which for this role includes
**self-hosted cluster operations** as well as Temporal Cloud. Workers and apps are the
"customer-owned" plane and always run locally on kind; the Temporal *server* is the
backend they connect to and can be either self-hosted or Cloud.

## Decision

Default the local backend to a **self-hosted Temporal server on kind**, deployed via the
official `temporalio` Helm chart backed by CloudNativePG (per the colleague reference), and
make **Temporal Cloud** selectable via connection profile (ADR-0005). `docker-compose.yml`
remains a no-Kubernetes quick-start that also runs a self-hosted server.

## Consequences

- Exercises self-hosted cluster ops (schema jobs, history shards, server metrics) and Cloud
  parity from the same codebase.
- More setup than a Cloud-only or compose-only approach; mitigated by reusing the
  colleague's chart values, 15-minute install timeout, and resource pinning for a 16 GB host.
- Workers/apps are backend-agnostic; switching backends is an env/profile change, not a code
  change.

## Update (2026-06-25): the app datastore also uses CNPG

When the app tier moved onto kind (orders-api + orders-db, `deploy/charts/orders-app`), its
PostgreSQL (**orders-db**, distinct from any Temporal cluster DB) was put on the **same
CloudNativePG operator** rather than a bare Postgres Deployment — one Postgres story across the
repo. The operator is a sync-wave −2 ArgoCD add-on (`deploy/charts/cloudnative-pg`, pinned in
`config/dependencies.yaml`); orders-app declares a `postgresql.cnpg.io/v1 Cluster` (primary +
replica, auto-failover). State lives on kind's `local-path` PVC; lifecycle + reset semantics are
documented in `docs/RUNMODES.md`. Applies on the kind path regardless of whether the *Temporal*
backend is Cloud or self-hosted.

## Update (2026-07-06): OSS-on-kind wired; toggle + decoupled server lifecycle

Self-hosted-on-kind is now implemented (`deploy/charts/temporal-server` — a wrapper over the official
`go.temporal.io/helm-charts` chart, vendored as a subchart, plus CNPG Postgres, cert-manager frontend
mTLS (ADR-0008), and an Argo-managed bootstrap Job from `config/temporal/namespaces.yaml`).

- **Single control point:** a `temporal_backend` (`cloud`|`oss`) Terraform var in the cluster layer,
  driven by `just platform-up backend=…` (fresh) and the guarded `just switch-backend` (live). The
  connection contract is unchanged — `tls` stays on both backends; only the credential type differs
  (Cloud API key ↔ OSS mTLS client cert). `numHistoryShards=512`, tunable, immutable in-place with
  `just temporal-db-reset` as the local re-pick escape hatch.
- **Decoupled server lifecycle:** the OSS server's existence is a separate `oss_server_enabled` var,
  NOT gated on `temporal_backend` — switching workers to Cloud never prunes the server (its state
  survives; swap-back is instant). Teardown is the explicit `just temporal-server-down`.
- **Default deferral:** this ADR intends self-hosted as the eventual *default* backend. That flip is
  deferred — **Cloud remains the default** (`temporal_backend="cloud"`) until the OSS path is proven
  in live use; only then does the default move. No hard switch of the supported path yet.
