# 0014 — Console backend-awareness (real Cloud probe), Cloud-safe reset, Compose demoted

- **Status:** **LANDED + LIVE-VALIDATED ON KIND+CLOUD (2026-06-25).**
- **Date:** 2026-06-25
- **ADRs:** **ADR-0015** extended (phase-2+) — see its "Update (checkpoint 0014)" note. Builds on
  0013.

## Why

After 0013 the architecture page reads live kind state, but it still rendered for the old
Compose-OSS world: the "Temporal Cloud" box derived health from local `temporal`/`postgresql`/
`temporal-ui` containers that don't exist on kind+Cloud (so it read `unknown`), host-plane cluster
tooling (ArgoCD, Headlamp, viz-proxy, codec-server) wasn't shown, and `pgweb` was down on kind (its
container lived only in the `host-apptier` overlay, which kind omits). Separately, the repo still
advertised running Temporal workers / OSS / the app tier on Docker Compose as a goal, which no
longer matches reality (workers + app tier run on kind).

## Decisions (from the planning conversation)

- **Cloud status = a real, console-owned probe** (not orders-service). Cloud liveness is a platform
  concern; owning it in one business app would force every future domain to duplicate it. Two
  signals: my **namespace/region reachability** (read-only SDK `check_health` from the injected
  Cloud profile) + the **public Temporal Statuspage**.
- **Reset Demo State on Cloud = local data only.** Never terminate or delete workflows in a managed,
  shared Cloud namespace.
- **Host-plane tools** shown in the existing **substrate-aware Tooling strip** (no diagram rework).
- **Compose:** cut the Temporal **workers** (delete `compose/workers.yml`); keep the OSS **server +
  app tier** as an honest legacy fallback (`just up`, no workers). Drop `up-cloud`/`up-cloud-prod`
  (their point — compose workers vs Cloud — was the demoted two-fleets footgun).

## Done this session

- **Backend descriptor `CONSOLE_BACKEND` (cloud | oss).** Mirrors `CONSOLE_SUBSTRATE`. Base
  `docker-compose.yml` defaults `cloud`; `compose/oss-server.yml` sets `oss`. Plumbed into
  `config.py` (`console_backend`) and the status loop.
- **`CloudStatusProvider`** (`app/services/status/cloud.py`): builds its own lazy read-only
  `temporalio` client from `TEMPORAL_ADDRESS/NAMESPACE/TLS/API_KEY` (passed through to the console
  service in the base compose, sourced by `up-cloud-kind`), checks namespace reachability with
  **`DescribeNamespace`**, and fetches the Statuspage summary; combines them into one
  `temporal-cloud` entry (unreachable→down, platform incident→degraded, else healthy).
  **Gotcha (found in live validation):** Temporal Cloud API keys are **not** authorized for the
  gRPC health service — `service_client.check_health()` returns `RPCError: Request unauthorized`.
  `DescribeNamespace` (which the worker key *is* scoped for) is the authorized, more informative
  reachability signal — it also confirms the exact Terraform-provisioned namespace. Wired in `services/status/__init__.py` via a new `RootProvider`
  that merges the cloud probe onto the substrate base and applies `OSS_ONLY_KEYS` /`KIND_ONLY_KEYS`
  exclusions. Registry (`core.py`) gains `temporal-cloud`, `headlamp`, `argocd` (kube locator),
  `viz-proxy`, `codec-server`.
- **Architecture page** (`templates/architecture.html`): `archApp(backend)`; Cloud box branches on
  backend (real probe + status-page link on cloud, OSS-internals expander on oss); the
  `__temporal_cloud__` tooltip shows endpoint/namespace/latency/platform line on cloud; the Tooling
  strip is **data-driven** (`toolingKeys()` over snapshot `group==='Tooling'`); the reset modal
  hides the terminate/delete-history options on cloud. `routes/pages.py` passes `backend`.
- **Cloud-safe reset:** `orders.api` `/admin/reset` gains `local_only` (skips the Temporal pass,
  truncates tables only); the console `/reset` proxy sends `local_only=true` on the cloud backend.
- **pgweb reconnected:** `pgweb-orders` moved into the base compose pointed at the kind-mapped
  orders DB (`host.docker.internal:5433`, CNPG owner admin/password); the `host-apptier` overlay
  overrides `DATABASE_URL` to the in-compose `orders-db` for the local-OSS path.
- **Compose demotion:** deleted `compose/workers.yml`; `oss-server.yml` drops the worker re-attach
  fragments and sets `CONSOLE_BACKEND=oss`; `pyproject.toml`/`justfile` drop `workers.yml` from
  `up`/`down`/`fresh` and remove `up-cloud`/`up-cloud-prod`/`fresh-cloud`; headers reworded. Docs
  (README, RUNMODES, ARCHITECTURE, OBSERVABILITY, ADR-0015) reframed: kind+Cloud is the supported
  path, Compose = host plane + legacy OSS server+app (no workers).

## Verification

- **Static (done):** `docker compose config -q` valid for base alone (substrate kind / backend
  cloud) and base + host-apptier + oss-server (substrate compose / backend oss); confirmed the
  overlay repoints pgweb at `orders-db:5432` and sets `CONSOLE_BACKEND=oss`, base resolves pgweb to
  `host.docker.internal:5433` and `CONSOLE_BACKEND=cloud`; no `workers.yml` references remain.
  `poe lint` green (ruff/format/pyright 0 errors; helm lint ×3; kubeconform 11/7).
- **Live (DONE, kind+Cloud):** rebuilt the host plane (`up-cloud-kind`) and redeployed orders-api
  (`platform-up`, new digest synced by ArgoCD). Confirmed on `:8086`:
  - `temporal-cloud` node **healthy** via the real probe (`status_source=cloud`): namespace
    `ziggymart-nonprod.evvjb` reachable (24ms via DescribeNamespace), statuspage "All Systems
    Operational". Negative test: a bogus API key → connect/describe fails → `reachable=False`
    → `down`.
  - Cluster workloads + **argocd** kube-sourced healthy; Tooling strip data-driven shows
    console/lgtm/**pgweb-orders**/headlamp/viz-proxy/codec-server; OSS-only nodes (temporal,
    temporal-ui, postgresql, ui-proxy, pgweb-temporal) correctly **absent** on kind+Cloud.
  - **pgweb** connected to the in-cluster CNPG orders-db ("Connected to PostgreSQL 18.3").
  - **Cloud-safe reset:** `POST /admin/reset?local_only=true` (and the console `/api/reset` button
    path) returned `local_only:true, workflows:null`, truncated `orders`/`idempotency_keys`, and
    left Cloud workflows **unchanged (8 → 8)** — no terminate, no delete against the managed
    namespace.

## Follow-up landed this session — multi-region/namespace Cloud inventory + observer key

- **Read-only observer identity (Terraform).** `deploy/terraform/layers/cloud/observer.tf`:
  account-scoped `read` service account + API key, **plus per-namespace `read`** (account role
  alone returns an empty `GetNamespaces` — a read-only principal only sees assigned namespaces).
  Least privilege: read-only everywhere. Outputs `observer_api_key_token` (sensitive). This is the
  dedicated observer key the prior session flagged as a follow-up — now real.
- **Cloud Ops API in the console.** `CloudStatusProvider` (cloud.py) gains a `CloudOperationsClient`
  (`saas-api.tmprl.cloud`) using `TEMPORAL_CLOUD_OPS_API_KEY`; throttled (~60s) `GetNamespaces` +
  `GetRegions` attach `regions`/`namespaces` to the `temporal-cloud` entry. "Regions used" is
  computed from the namespaces (the catalog returns all ~20 Cloud regions). The architecture page
  renders Regions + Namespaces sub-blocks in the Cloud box (per-namespace state/active-region,
  "this" badge on the console's own namespace); hidden when no observer key.
- **Gotchas (live):** Ops API needs a `temporal-cloud-api-version` header (`version="v0.16.0"`, the
  bundled proto VERSION; env-overridable) or it returns "cloud API version must be specified".
- **Live-verified:** minted the observer key (`terraform apply -target`), restarted the console with
  it — snapshot showed `regions used = [aws-us-east-1 / US East (N. Virginia)]` and namespaces
  `ziggymart-nonprod` (this) + `ziggymart-prod`, both ACTIVE→healthy. (Namespaces later collapsed
  to a single `ziggymart` by checkpoint 0015.)
- **UI polish:** the Temporal logo watermark is hidden when the inventory block is present (it was
  floating over the new data); each region chip carries a status dot derived from the namespaces
  resident in it (`regionStatus()`).

## Next / follow-ups

- **Live-validate on kind from scratch**, then commit.
- **Dedicated read-only Cloud observer API key** (cloud Terraform layer) instead of reusing the
  worker key for the console probe — mirrors the `console-reader` SA on the kube side.
- **kind metrics** still unwired (workers no longer emit to Compose Prometheus); pod-scrape + the
  Cloud OpenMetrics endpoint is the open observability follow-up.
- **OSS-on-kind**: once it lands, the legacy Compose OSS server+app fallback can retire and the
  OSS-internals rendering can become kube-sourced.
