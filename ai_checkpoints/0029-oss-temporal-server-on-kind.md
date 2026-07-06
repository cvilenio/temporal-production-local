# Checkpoint 0029 — OSS Temporal Server on kind + single-toggle Cloud↔OSS swap

**Date:** 2026-07-06
**Status:** **Landed + live-verified (kind + OSS).** All static gates green (helm template both
modes, `terraform validate`, `go build`/`vet`, `ruff`, `versions-audit`) AND a full end-to-end
bring-up validated: OSS server Healthy, workers + orders-api + autoscaler connect over **mTLS**,
2 `OrderWorkflow`s ran to **Completed** with custom search attributes upserted + queryable, Worker
Controller versioning **Pinned** on OSS, Temporal Web UI reachable via the console iframe. Five
integration issues were found + fixed during the live run (see "Live validation" below).

## Why

Two use cases (ADR-0003): (1) run Temporal fully locally so worker/workflow **load tests** don't
hammer Cloud, and (2) demonstrate a basic OSS-on-k8s deployment. The repo was designed backend-
agnostic from the start (ADR-0003/-0005/-0007/-0015) and OSS-on-Compose already worked — the missing
piece was OSS-on-kind, wired behind a clean single toggle with full feature parity so the two
backends swap cleanly.

## What landed

- **`deploy/charts/temporal-server`** — a wrapper over the official `go.temporal.io/helm-charts`
  chart (vendored as a subchart at publish time, so ArgoCD pulls one self-contained chart offline).
  Adds CNPG Postgres (pre-creates both DBs), cert-manager **frontend mTLS** (ADR-0008), a bootstrap
  Job (namespace + search attributes from `config/temporal/namespaces.yaml`), and pod scrape
  annotations. `numHistoryShards=512` (small-prod standard), a tunable value; the `internal-frontend`
  service isolates internal traffic from external client-auth. ArgoCD app uses `releaseName=temporal`
  so services render as `temporal-frontend`/`temporal-web`/etc.
- **`temporal_backend` toggle** (cluster layer): `remote-state.tf` gates the Cloud state read and
  derives every connection value; `tls` stays **on** both backends, only the credential ref switches
  (Cloud `apiKeySecret` ↔ OSS `mtlsSecret`). The OSS server's existence is a **decoupled**
  `oss_server_enabled` var (switching to Cloud never prunes it).
- **mTLS end-to-end:** appkit gained `temporal_tls_server_ca_cert_path`; the workers / orders-api /
  autoscaler charts mount the cert-manager client cert + set the cert/key/CA paths; the Go autoscaler
  `Dial` loads client cert + RootCAs.
- **Operator interface:** `just platform-up oss` (fresh), the guarded **`just switch-backend
  <cloud|oss>`** (live — detects in-flight workflows, y/n prompt, `--drain`/`--yes`, reuses image
  digests, recreates the console with the target profile), `just temporal-server-down` +
  `just temporal-db-reset` (explicit destructive), `just up-oss-kind`, `config/local-oss-kind.env`.
- **Observability:** the Cloud OpenMetrics scrape + secret mount are no longer committed in
  `prometheus.yaml`; the cluster layer injects a backend-specific scrape (`temporal-cloud` vs
  `temporal-oss`). Self-hosted-internals dashboards light up on OSS; dual-sourced Critical Flows
  render on either; pure-Cloud dashboards leave-dark on OSS.
- **Console:** kube locators for the OSS server trio (namespace `temporal`), backend-neutral
  "Temporal" group + backend-aware boundary label/caption, Compose-only dev UIs excluded on kind, and
  the Temporal Web UI fronted by the viz-proxy (`:8089`, frame-stripped) → console iframe.
- **Versions:** SDK bumped to latest stable (1.30.0); official chart pinned (1.5.0 / server 1.31.1)
  in `config/dependencies.yaml` with a `versions-audit` lockstep assertion.

## Live validation (the remaining gate)

`just up-oss-kind` → `just platform-up oss`; confirm ArgoCD Synced/Healthy incl.
`temporal-server`, the bootstrap Job Completed, workers connect over mTLS (`TEMPORAL_TLS=true`, cert
mounted, no API key — check the rendered pod), an E2E order executes + shows in the embedded Web UI,
self-hosted-internals dashboards have data, then drive load and confirm the autoscaler scales against
OSS with zero Cloud action consumption. Finally exercise `just switch-backend` both directions
(clean + in-flight y/n) and `just temporal-server-down`. See `docs/RUNMODES.md`.

## Live validation — issues found + fixed

The static gates passed but the live bring-up surfaced five real issues, all fixed:

1. **`just` arg parsing** — `just platform-up backend=oss` parsed `backend=oss` as a positional
   arg / second recipe. Fixed: positional recipe param, invoked `just platform-up oss`.
2. **`ruff format` gate** — my edited files needed the formatter (distinct from the `ruff check`
   linter I'd run). Fixed: formatted.
3. **Schema-job hook deadlock (the big one)** — the upstream chart ships schema setup as a Helm
   `pre-install` hook → ArgoCD runs it in PreSync, *before* any sync-wave, so it fired before the
   CNPG Postgres existed and hung on the missing DB/secret. Fixed with the chart's documented
   Terraform/Argo escape hatch: `schema.useHelmHooks: false` + pin the (now normal) schema job to
   `sync-wave: -1` (after CNPG at -2). Also `shims.{dockerize,elasticsearchTool}: false` for 1.31.
   Recovery from the wedged first attempt required deleting the stuck ArgoCD app + hook-finalizer.
4. **Worker mTLS mount conflict** — the Worker Controller ALREADY mounts the `mutualTLSSecretRef`
   cert at `/etc/temporal/tls` and sets `TEMPORAL_TLS_CLIENT_CERT_PATH`/`KEY_PATH`. My chart added a
   *second* mount at the same path → "volumeMounts must be unique", so no worker pods spawned. Fixed:
   dropped the chart's volume+mount; kept only `TEMPORAL_TLS_SERVER_CA_CERT_PATH` env (the one the
   controller does NOT set — required to trust the self-signed CA). (A prior revision had also
   mis-placed the volumeMounts mid-`env:` list; corrected.)
5. **Search-attribute propagation race** — the bootstrap Job registered attrs immediately after
   `namespace create`, hitting "Namespace not found" (namespace-cache propagation lag). Fixed: the
   Job now waits until `search-attribute list` succeeds before registering (mirrors the Compose
   bootstrap). For the already-running cluster the four attrs were registered manually.

6. **Web UI 500 (temporal-web → mTLS frontend)** — the Web UI is a gRPC client of the frontend;
   pointed at the external `temporal-frontend:7233` (mTLS-required) with no client cert, its API
   calls returned 500/503 ("connection reset"). Fixed with the chart's `web.temporalAddress:
   temporal-internal-frontend:7236` (the non-mTLS internal endpoint). NOTE: an initial attempt via
   `web.additionalEnv` (a second `TEMPORAL_ADDRESS`) worked at kubelet level but broke **ArgoCD's
   structured-merge diff** — duplicate env keys → `ComparisonError` → the app couldn't sync at all.
   Use the chart's dedicated value, never a duplicate env, for ArgoCD-managed resources.

**Autoscaler on OSS — confirmed.** Its env targets `temporal-frontend.temporal.svc:7233` over mTLS
(client cert + CA loaded by the Go `Dial`), and the WorkerAutoscaler CR status reads "Backlog signal
reachable" for both queues — it successfully polls the OSS frontend's `DescribeWorkerDeploymentVersion`.
No Cloud dependency; scales off the local server under load.

Final chart versions after fixes: temporal-server `0.1.4`, orders-workers `0.1.15`, orders-api
`0.1.4`, temporal-worker-autoscaler `0.1.4`.
