# ADR-0016: Terraform-vs-ArgoCD boundary, and deadlock-resilient dependency ordering

- **Status:** Accepted
- **Date:** 2026-06-25
- **Related:** ADR-0002 (infra & delivery), ADR-0004 (worker versioning), ADR-0008 (auth
  identities), ADR-0009 (cluster-layer seam). Prompted by the live-validation incident in
  `ai_checkpoints/0011` (an ArgoCD sync deadlock + a downstream-config bug that every static
  check missed).

## Context

Bringing the app tier onto kind (orders-api + orders-db, checkpoint 0011) surfaced ArgoCD's
nastiest failure mode — the **sync deadlock** — and a question of where the
Terraform/ArgoCD boundary should sit. The concrete incident: the `orders-db` CNPG `Cluster`
(sync-wave −1) consumed a credential `Secret` left at the default wave (0). initdb failed
`secret not found`, the Cluster never went healthy, and the Application's sync operation
**hung at wave −1 forever**. A hung op is not a failed op, so `selfHeal` and hard-refresh never
re-ran it — even after the chart was corrected. Separately, the workers dialed `localhost`
(a wrong default `ORDERS_SERVICE_URL`) and every workflow failed, while the workers still
reported healthy.

Two design questions fell out, and a tempting-but-wrong answer to avoid.

**Tempting wrong answer:** "move the dependency into Terraform so ArgoCD can't deadlock on it."
That erodes GitOps for no good reason and mis-locates ownership. We reject it. The fixes below
keep everything reconcilable in ArgoCD.

## Decision

### 1. The Terraform ↔ ArgoCD boundary is decided by *provenance*, not convenience

- **Terraform owns:** the cluster's existence; ArgoCD itself (bootstrap); and **secrets that
  cannot live in git and are minted by an external authority** — Temporal Cloud API keys, the
  account id. TF's only role beyond bootstrap is **credential courier**: carry an
  externally-minted secret into the cluster, because git can't hold it (pre-commit blocks it)
  and ArgoCD can't reconcile it (ArgoCD reads git, not Temporal Cloud).
- **ArgoCD owns:** every workload and all config whose desired state *can* be expressed in git —
  including git-safe secrets. Example: the `orders-db` owner password is a local-dev value
  already in `docker-compose.yml`; it has no external minter, so it stays in the chart (ArgoCD),
  **not** Terraform. Putting it in TF to dodge a deadlock would be using TF as a workaround.

Litmus test for a secret: *who mints it?* External system (Cloud) → Terraform couriers it.
Us / git-safe → ArgoCD.

### 2. Order *existence* dependencies with sync-waves; handle *readiness* dependencies with k8s readiness — never a sync-wave

- **Existence dependency** — B cannot be admitted/initialised without A (a CR needs its CRD; a
  CR needs its operator; a CNPG `Cluster` mounts its credential Secret at initdb). These flip
  "ready" instantly and **belong in sync-waves**: put the dependency in an *earlier* wave.
  This is the legitimate, in-ArgoCD use of waves. (Fix applied: orders-data's Secret → wave −1,
  Cluster → wave 0.)
- **Readiness dependency** — B needs A to be *serving* (orders-api needs orders-db serving; the
  console needs orders-api up). This can take minutes and fail transiently. **Gating it with a
  sync-wave is the deadlock trap** — ArgoCD blocks the whole app on health that may never come,
  and a hung sync doesn't self-heal. Handle it with **k8s-native readiness**: let the dependent
  deploy in parallel and crash-loop / stay NotReady until the dependency serves. A NotReady pod
  is removed from its Service endpoints (callers route nowhere, not to a broken backend), is
  visible, and self-heals. No deadlock.

### 3. Decompose Applications along failure domains

One Application per failure domain, so a stuck sync's blast radius is one domain, not a shared
wave sequence. orders-app was split into **orders-data** (CNPG Cluster + its git-safe Secret,
existence-ordered within the app) and **orders-api** (Deployment + Service). Between them there
is **no sync gate** — orders-api depends on orders-db at runtime (k8s readiness), so a slow DB
makes orders-api crash-loop-until-ready, never deadlock a shared op.

### 4. Make the health signal meaningful — three gating layers protect business logic during decoupled bring-up

Decoupling creates a warm-up window where partially-ready resources exist. Three layers keep
business logic from running against not-ready dependencies, all **runtime** (not deploy-wave):

- **k8s readiness = the networking gate.** A NotReady pod gets no Service endpoints. orders-api
  binds (and so passes its TCP readiness) only after its lifespan connects DB + Temporal, so its
  `orders-service` endpoints appear only when it can serve.
- **Temporal Worker Versioning / Build ID = the task-routing gate.** A new worker version
  receives no workflow tasks until promoted to **Current**, and the Worker Controller only
  promotes a *healthy* (pods-Ready) Deployment. So tasks never route to a half-ready fleet; the
  old Current version keeps serving (ADR-0004).
- **Activity retries + idempotency = the correctness backstop.** A task landing mid-window fails
  and retries with backoff; the workflow survives as long as the retry horizon outlasts the
  window, and idempotency keys + saga compensation prevent double-processing. So decoupling costs
  at most a few transient *failed attempts*, never incorrect state.

The gate is only as good as the health signal. A worker that is "process-up + Temporal-connected"
but can't reach its downstream (the `localhost` bug) would still be promoted and fail every task.
So: **a startup-only downstream-reachability check feeds the health signal.**

### 5. Dependency health checks are STARTUP-only, never continuous

Checking dependencies at **startup** (gate initial readiness/promotion) is good practice and
cheap (N pods × one check, spread over the rollout). Re-checking dependencies on every
**readiness/liveness tick is an anti-pattern** — it amplifies load (N × frequency × forever) and
**couples failure domains** (a downstream blip fails all probes → all pods NotReady → cascade).
The k8s/SRE guidance: liveness/readiness check the pod *itself*, not its dependencies.

Reconciliation: use a **`startupProbe`**. It runs only at container start, gates readiness until
it passes, then stops (readiness/liveness take over, local-only). The workers carry a
`startupProbe` doing a *lightweight TCP connect* to their configured `ORDERS_SERVICE_URL` +
`MOCK_API_URL` (read from env, so it always matches actual config). A misconfigured version
never passes startup → never Ready → never promoted (Build-ID gate) → lands in
`CrashLoopBackOff` and simply never takes traffic — instead of every workflow failing. The
strongest form is Progressive rollout + a gate workflow (ADR-0004); the startupProbe is the
lightweight default.

Asymmetry by role: **in-cluster apps crash-loop-until-ready** (k8s recovers, NotReady gates
endpoints). The **console is boot-resilient / degrades** — it must run *before* the cluster
exists and survive losing everything outside the host plane, so its DB pool is non-fatal and
self-healing (never crashes on an unreachable dependency).

## Enforcement

- **CI gate** (`deploy/check-sync-waves.py`, wired into `deploy/lint-manifests.sh` → `poe lint`):
  fails if any rendered resource references a Secret/ConfigMap in an equal-or-later sync-wave.
  Catches the exact deadlock class before it reaches a cluster. References to externally-provided
  secrets (another Application, or TF-seeded) are skipped — their existence is guaranteed before
  the app syncs (rule 1/3).
- **Detection + runbook** (see `docs/runbooks/argocd-stuck-sync.md`): alert on any Application
  `Progressing` beyond a threshold (turns a silent deadlock into a page), and unstick with
  `argocd app terminate-op` — the op-terminating action that hard-refresh does *not* perform.

## Consequences

- ArgoCD stays the one-stop shop for in-cluster state; Terraform stays minimal (bootstrap +
  external-secret courier). GitOps is preserved, not hollowed out.
- More Applications to manage (orders-data, orders-api split), in exchange for isolated failure
  domains and no shared-sync deadlock.
- The deadlock *class* is removed (CI gate + the existence-vs-readiness rule), not just the one
  instance. Cold-start correctness during decoupled bring-up is protected by readiness + Build-ID
  routing + retries/idempotency, validated from a scratch cluster (see ai_checkpoints/0011).
