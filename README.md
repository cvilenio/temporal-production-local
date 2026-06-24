# temporal-production-local

> A production-shaped Temporal platform that runs entirely on your machine — `kind` +
> Terraform + ArgoCD, backed by Temporal Cloud. GitOps delivery, worker versioning, an
> observability and operator-visibility plane, and a real workflow use case — with the
> reasoning behind each choice made explicit.

## What this is

`temporal-production-local` is an opinionated, end-to-end example of running Temporal the
way a real platform team would — assembled into a single repository you can clone, stand
up locally, and tinker with freely.

Everything runs in a local Kubernetes cluster (`kind`). The one piece that lives in the
real world is the Temporal Service itself, provided by Temporal Cloud. The goal is to get
as close to a genuine production deployment as possible without provisioning (or paying
for) cloud infrastructure beyond Temporal Cloud.

This is one way to build a production Temporal platform — not the only way, and not a
canonical reference. It encodes a set of deliberate choices about tooling, structure, and
operational practice, with the reasoning made explicit so you can adopt, adapt, or argue
with it.

## Why it exists

This repository began as an onboarding capstone: a hands-on way to explore and demonstrate
production readiness — the domain I own as a Platform Architect at Temporal — by actually
building it rather than reading about it.

It is intended to outlive that original purpose. Two audiences:

- **Me** — a living workbench for exploring SDK behavior, versioning strategy, worker
  tuning, observability, and operational patterns across the Temporal stack.
- **Anyone working with production Temporal** — a concrete, runnable reference for how the
  pieces fit together, useful when onboarding to an account or reasoning about a
  customer's setup.

## Scope and philosophy

A few principles shape what belongs here and what doesn't.

**Production-shaped, fully local.** Every component is wired the way it would be in
production — GitOps delivery, declarative infrastructure, observability, encryption,
access control — and the entire stack stands up on a laptop. Where local and production
genuinely diverge, the divergence is documented rather than hidden.

**Temporal Cloud is the real backend.** The repository integrates against an actual
Temporal Cloud namespace. This keeps the integration honest: real TLS, real namespace
configuration, real codec endpoint registration, real per-user access semantics.

**Opinionated, with reasoning shown.** Tooling choices (`kind`, Terraform, ArgoCD) are
deliberate and explained. The intent is not to be exhaustive about every possible
toolchain, but to be coherent and defensible about one.

**Honest about its edges.** This is not a deployment running on real cloud compute, and it
doesn't pretend to be. The value is in the shape and wiring of a production system, made
fully reproducible and tunable on a single machine. The status table below states plainly
what is built and proven versus what is still planned — nothing here is claimed working
that isn't.

---

## What's built today vs. planned

This project is built in checkpoints (see `ai_checkpoints/`). The single proven,
end-to-end path is **kind workers + Temporal Cloud**. Be guided by this table, not by the
vision above:

| Capability | Status |
|---|---|
| **kind workers → Temporal Cloud** (ArgoCD + Worker Controller, digest-pinned) | ✅ **working — the flagship path**, live-verified |
| Terraform control plane (kind cluster, Cloud namespaces + API keys, ArgoCD) | ✅ working |
| GitOps delivery + worker versioning (Worker Deployment / Build IDs) | ✅ working |
| Operator visibility plane (console aggregating Temporal UI, Headlamp, ArgoCD UI) | ✅ working (kind + Cloud) |
| Local OCI delivery + offline-resumable cluster (zot, stop/start) | ✅ working |
| Retail order workflow (saga, signals, idempotent vs. write-then-verify retries) | ✅ working |
| **Observability / metrics on kind** | 🚧 **not wired / unproven** — metrics have only been exercised on the Compose-OSS path; treat kind metrics as not yet working |
| App tier (orders API, mock API, console) on kind | 🚧 planned — runs in Compose today |
| Self-hosted Temporal **server** on kind (the OSS backend) | 🚧 planned — not wired |
| Polyglot workers (Go / TypeScript / Java) | 🚧 planned — Python only today; the layout is polyglot-ready |
| Encryption codec + codec server (client-side decode, per-user access) | 🧱 scaffold only — placeholder codec; replace with real AEAD before any sensitive use (ADR-0006) |
| Codec proxy (payload encoding at the proxy layer) | 🚧 planned |
| Alerting | 🚧 planned |

### Run-mode matrix

"Apps" run on your laptop; "backend" is where the Temporal Service itself lives.

| Apps run on | Temporal backend  | Command                                  | Status |
|-------------|-------------------|------------------------------------------|--------|
| **kind**    | **Temporal Cloud**| `just platform-up` + `just up-cloud`     | ✅ **the supported path** |
| Compose     | Local OSS server  | `just up`                                | ⚠️ workflow runs; see caveat below |
| Compose     | Temporal Cloud    | `just up-cloud`                          | ⚠️ workflow runs; see caveat below |
| kind        | Local OSS server  | (in-cluster `temporal-server` chart)     | 🚧 planned — not wired |

> **Compose caveat (important).** The Compose paths still start and run the order
> workflow, and Compose-OSS is the only place metrics have been exercised. **But the demo
> console has evolved toward the cluster plane** — it now carries Headlamp and ArgoCD tabs
> that are inert without a kind cluster, and its run-mode awareness is tuned for the
> kind + Cloud path. Treat Compose as a workflow/SDK quick-start, **not** a polished
> end-to-end demo. The console is not (yet) tailored back to Compose.

---

## Getting started: 0 → kind workers on Temporal Cloud

This is the supported path. Budget ~15–20 minutes the first time (image builds and the
initial registry warm-up dominate). Every step is idempotent and safe to re-run.

### 1. Prerequisites

Install these CLIs (Homebrew names in parentheses):

- **Docker** (`docker`) with Docker Desktop or an equivalent daemon running.
- **kind** (`kind`) — local Kubernetes in Docker.
- **kubectl** (`kubectl`).
- **Terraform** ≥ 1.6 (`terraform`).
- **Helm** (`helm`).
- **crane** (`crane`, from `go-containerregistry`) — reads image digests for pinning.
- **jq** (`jq`).
- **just** (`just`) — the task front door.
- **uv** (`uv`) — the Python toolchain/runner.

You also need a **Temporal Cloud account** and a **bootstrap API key** with account-level
(namespace-admin) scope. Generate one in the Temporal Cloud UI (*Settings → API Keys*) or
with `tcld apikey create`.

> **Why a bootstrap key?** Terraform uses it once to create your namespaces and to mint the
> least-privilege *worker* API keys the workers actually run with. The bootstrap key is
> never committed and never reaches the cluster.

### 2. Put your Cloud credentials in `.secrets/` (never committed)

The `.secrets/` directory is git-ignored except for its layout placeholders. Create two
small env files there:

```bash
# Your Temporal Cloud account id (the short suffix), kept out of git.
echo 'export TF_VAR_account_id=<your-account-id>' > .secrets/account.env

# Your bootstrap (account-admin) API key, read by the Terraform provider.
echo 'export TEMPORAL_CLOUD_API_KEY=<your-bootstrap-key>' > .secrets/keys/bootstrap.env

chmod 700 .secrets
```

See [`.secrets/README.md`](.secrets/README.md) for the full layout and the rule that this
directory holds the only copy of some secrets (Terraform state lives here too).

### 3. Provision Temporal Cloud (namespaces, worker keys, search attributes)

This is the **control-plane base layer**. It pulls only the `temporalcloud` provider and
needs no cluster present.

```bash
cd deploy/terraform/layers/cloud
cp terraform.tfvars.example terraform.tfvars   # git-ignored; edit the overlay if you like
source ../../../../.secrets/account.env        # TF_VAR_account_id
source ../../../../.secrets/keys/bootstrap.env # TEMPORAL_CLOUD_API_KEY

terraform init
terraform plan -out=cloud.plan
terraform apply cloud.plan
cd -
```

This creates the `ziggymart-nonprod` / `ziggymart-prod` namespaces, a least-privilege
worker service account + API key per namespace, and the `OrderId` / `OrderStatus` /
`TraceId` custom search attributes — all from the shared spec in
`config/temporal/namespaces.yaml`. State is written to `.secrets/terraform/cloud.tfstate`
and **contains the worker API key in plaintext** — treat it as a credential. Full detail:
[`deploy/terraform/layers/cloud/README.md`](deploy/terraform/layers/cloud/README.md).

### 4. Bring up the kind cluster and deploy the workers

One command does the whole local lifecycle: create the kind cluster + local OCI registry,
mirror third-party charts, run the CI gate (lint, typecheck, test, build + push worker
images), publish the workers Helm chart, pin the workers by image digest, and apply the
cluster Terraform layer (which reads the Cloud layer's state, writes the worker API key as
a Kubernetes Secret, and seeds the ArgoCD Applications):

```bash
just platform-up
```

The cluster layer wires the workers to Cloud automatically — it reads the **regional**
Cloud endpoint and the worker API key from the Cloud layer's outputs and injects them via
a Secret that the Worker Controller mounts. By default the cluster mirrors the
`ziggymart-nonprod` namespace. The workers are delivered as a
`temporal.io/WorkerDeployment`, so the deployed version is content-addressed by digest.

Verify the workers reconciled:

```bash
just k get applications -n argocd            # orders-workers should be Synced/Healthy
just k get pods -n orders                    # workflow + activity worker pods Running
```

### 5. Bring up the app tier (Compose) pointed at the same Cloud namespace

The orders API, mock external API, and console run in Compose for now. Point them at your
Cloud namespace by writing a worker connection profile from the Cloud layer outputs, then
start the stack:

```bash
# Derive the nonprod connection profile from the Cloud layer outputs.
cd deploy/terraform/layers/cloud
cat > ../../../../.secrets/keys/cloud-nonprod.env <<EOF
export TEMPORAL_ADDRESS=$(terraform output -json endpoints          | jq -r '.["ziggymart-nonprod"]')
export TEMPORAL_NAMESPACE=$(terraform output -json namespace_handles | jq -r '.["ziggymart-nonprod"]')
export TEMPORAL_TLS=true
export TEMPORAL_API_KEY=$(terraform output -json api_key_tokens     | jq -r '.["ziggymart-nonprod"]')
EOF
cd -

just up-cloud          # app tier, sourcing the profile above
just headlamp-reload   # nudge the cluster explorer to pick up the kubeconfig (optional)
```

> **Known edge — two worker fleets.** `just up-cloud` currently also starts the Compose
> worker containers, which poll the **same** Cloud namespace and task queues as the kind
> workers. Orders still complete, but either fleet may execute them — so this does not by
> itself *prove* the kind workers did the work. Until a Compose app-tier-only override
> lands, confirm execution on the kind side with
> `just k logs -n orders -l app.kubernetes.io/name=orders-activity` (or watch the kind
> pods) rather than assuming.

### 6. Open the consoles and run an order

| UI | URL | What it is |
|---|---|---|
| **Demo Console** | http://localhost:8086 | Operator UI — trigger orders, watch live status, jump to every other UI. |
| **ArgoCD** | http://localhost:8088 | GitOps delivery state for the workers (framed via the console). |
| **Headlamp** | http://localhost:8087 | Kubernetes cluster explorer (pods, logs) for the kind cluster. |
| **Temporal Cloud UI** | (link-out from the console) | Workflow history — the hosted Cloud UI opens in a new tab. |

From the **Demo Console**, open the Orders page, select **"Happy Path"**, and click
**Trigger scenarios**. The order is orchestrated by the kind workers against your Cloud
namespace; watch it complete in the console and inspect its history in the Cloud UI.

> Metrics/observability (Grafana) are wired only on the Compose-OSS path today and are
> **not** yet proven on kind — see the status table.

### Going offline (planes, demos with no network)

A *warmed* cluster runs fully offline — **stop, don't delete**:

```bash
just cluster-stop     # docker stop nodes + registry; all state preserved
# ... offline ...
just cluster-start    # resumes with zero network
```

`just cluster-down` deletes the cluster (for reclaiming resources) and a deleted cluster
**cannot** be recreated offline. The air-gap contract is detailed in
[`docs/RUNMODES.md`](docs/RUNMODES.md) and ADR-0013.

---

## Other run modes

The fastest path to *see the workflow run* — no Kubernetes, self-hosted Temporal in
Compose (read the Compose caveat above first):

```bash
just up        # local OSS Temporal server + app + LGTM observability
just down      # stop and drop volumes
```

Or run the app tier in Compose against Temporal Cloud (no kind): `just up-cloud`. The full
backend × topology matrix, the connection-profile contract, and the direnv footgun are
documented in [`docs/RUNMODES.md`](docs/RUNMODES.md).

---

## What the workflow demonstrates

The retail `OrderWorkflow` is intentionally built to show production patterns, not a toy
happy path:

- **Three-layer write per step.** Each logical step (e.g. "create shipment") is three
  independently-retried activities: the external call, the Postgres persist, and the
  customer-facing status update.
- **Idempotent vs. non-idempotent retries.** Idempotent external calls (payment,
  inventory) lean on the **Temporal retry policy**; the non-idempotent shipping call uses
  a **workflow-level write-then-verify** loop to avoid duplicate side-effects.
- **Saga compensation** on unrecoverable failure (e.g. release reserved inventory).
- **Signal-driven cancellation** of in-flight orders, with compensation.
- **Search attributes** (`OrderId`, `OrderStatus`, `TraceId`) so operators can filter
  workflows by business state.
- **Deterministic failure injection** via magic strings in the order payload (`Ghost`,
  `Flaky`, `Lost`) to reproduce ambiguous-side-effect, transient-503, and
  unrecoverable-timeout scenarios on demand.

The full scenario walk-through lives in [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md). The
order-ID model and worker topology are described in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Repository layout

```
apps/        Thin deployment units, grouped by class: temporal/ (workers, codec scaffold),
             business/ (orders API), demo/ (console, mock API).
libs/        Shared-kernel code apps import (the orders domain: workflows, activities,
             clients, DB, telemetry). Polyglot-ready; Python today.
images/      One configurable Dockerfile per language.
deploy/      How it ships: terraform/ (control plane), argocd/ (app-of-apps), charts/.
config/      Connection profiles + the shared namespace/dependency specs.
compose/     Self-hosted Temporal + observability for the no-Kubernetes quick-start.
docs/        ARCHITECTURE.md, RUNMODES.md, DEMO_SCRIPT.md, SHIP_PLAN.md, and adr/.
ai_checkpoints/  Cross-session work log (read newest-first for current state).
```

---

## Documentation map

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the durable target design (two planes,
  connection profiles, worker versioning, observability model).
- [`docs/RUNMODES.md`](docs/RUNMODES.md) — every run mode, backend selection, the offline
  contract.
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — the guided scenario walk-through.
- [`docs/SHIP_PLAN.md`](docs/SHIP_PLAN.md) — a sample 30–60 day Cloud rollout plan.
- [`OBSERVABILITY.md`](OBSERVABILITY.md) — the observability model (proven on Compose-OSS;
  see the status table for kind).
- [`docs/adr/`](docs/adr/) — numbered decision records (the *why* behind each choice).
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — commit conventions and contribution flow.

---

*This is a personal project built to explore and demonstrate production Temporal patterns.
It is not an official Temporal reference implementation.*
