# 0012 — GitOps boundary, deadlock-resilient ordering, app-tier split, worker-ownership fix

- **Status:** **LANDED + LIVE-VALIDATED FROM SCRATCH (2026-06-25).** A full `cluster-down` →
  `platform-up` → order-completes cycle ran with **zero manual intervention**. Not yet committed.
- **Date:** 2026-06-25
- **ADRs:** new **ADR-0016** (Terraform↔ArgoCD boundary + deadlock-resilient dependency ordering);
  **corrected ADR-0004** (the `temporal.io/ignore-last-modifier` annotation does NOT exist — it
  was a myth in an earlier draft; documented the real `ManagerIdentity` mechanism + release-on-
  teardown). Extends 0011.

## Why

Live-validating 0011 hit ArgoCD's worst failure mode (a **sync deadlock**) and prompted a design
discussion on the Terraform-vs-ArgoCD boundary. The user pushed back — correctly — on my first
instinct (seed the secret in Terraform), since that erodes GitOps. That produced a durable rule
set, a chart restructure, a CI gate, and (separately) the resolution of a pre-existing
worker-promotion trap.

## Decisions (see ADR-0016)

- **TF↔ArgoCD boundary by provenance:** Terraform owns cluster bootstrap + couriering
  externally-minted secrets that can't live in git (Cloud API keys). ArgoCD owns all in-cluster
  state, including git-safe secrets (the orders-db password stays in the chart — *not* TF).
- **Order existence deps with sync-waves; readiness deps with k8s readiness — never a wave.** A
  CR needing its Secret/CRD = existence (wave-order it). orders-api needing the DB serving =
  readiness (crash-loop-until-ready; no sync gate, no deadlock).
- **Decompose Applications by failure domain.** A stuck sync's blast radius = one domain.
- **Health signal must be meaningful;** dependency checks are **startup-only**, never continuous
  (continuous dep-probing cascades). Three runtime gates protect business logic during decoupled
  bring-up: k8s readiness (networking), Temporal Build-ID promotion (routing), retries+idempotency
  (correctness).

## Done this session

- **Split `orders-app` → `orders-data` + `orders-api`** (two ArgoCD Applications, two charts).
  orders-data = CNPG Cluster + its git-safe credential Secret (Secret wave −1 before Cluster wave
  0 — the 0011 deadlock fix, now isolated). orders-api = Deployment + Service + KSA, no sync gate
  on the DB (k8s readiness). TF `applications.tf`, chart-version vars, `chart-publish`,
  `lint-manifests.sh`, RUNMODES updated; old `orders-app` chart deleted.
- **Console boot-resilience (`apps/platform/console/python/app/db.py` + `main.py`).** DB pool is
  non-fatal at startup with a background reconnect maintainer; read paths degrade to empty. The
  console now boots Healthy with the entire kind side absent and self-heals when orders-db
  appears/returns — so it can run *before* an agent orchestrates kind (the explicit requirement).
- **CI sync-wave gate (`deploy/check-sync-waves.py` → `lint-manifests.sh` → `poe lint`).** Fails
  if any rendered resource references a Secret/ConfigMap in an equal-or-later wave. Catches the
  0011 deadlock class statically; negative-tested.
- **Worker downstream startupProbe (orders-workers chart).** Startup-only TCP check of the
  configured `ORDERS_SERVICE_URL` + `MOCK_API_URL`; a misconfigured version never goes Ready →
  never promoted (Build-ID gate) → old version keeps serving. Would have caught 0011's localhost
  bug as "never promoted" rather than "all orders fail."
- **Worker-deployment ownership trap — root-caused + durably fixed.** The controller's routing-
  ownership `ManagerIdentity` is suffixed with the **`temporal-system` namespace UID**, which is
  regenerated on every fresh kind cluster. Since the Cloud namespace + Worker Deployments persist
  across `cluster-down`, a rebuilt controller couldn't reclaim a deployment owned by the prior
  cluster's controller — Current stuck on a dead version, workflows pending. **The
  `ignore-last-modifier` annotation is a myth (verified against v1.7.0, latest).** The supported
  mechanism is `temporal worker deployment manager-identity unset`. The namespace-UID suffix is
  *deliberate* (per-cluster ownership safety), so the fix is **release-on-teardown**, not pinning
  identity: `just cluster-down` now runs `just release-worker-deployments` (best-effort, Cloud-
  only) to unset `ManagerIdentity` before deleting the cluster. ADR-0004 corrected; runbook added.
- **ADR-0016 + `docs/runbooks/argocd-stuck-sync.md`** (stuck-sync diagnosis, `terminate-op`,
  worker-ownership unblock via `manager-identity unset`, alert sketch on Apps Progressing>N).

## Verification

- **Static:** `poe lint` green — ruff/format/pyright 0 errors; helm lint ×3; **sync-wave gate**
  ok ×3 (negative test fails as expected); kubeconform 5/2/3/6; `terraform validate` clean.
- **Live, FROM SCRATCH (the key proof):** `just cluster-down` released ownership (graceful
  decommission) → `just platform-up` on a fresh cluster → orders-db bootstrapped with **no manual
  Secret**, the new controller **claimed the empty identity and promoted the live version with no
  manual step**, all key apps Healthy. Order `ORD-F41QH2QXXPR62KXX` ran `pending →
  shipment_created → completed`. The host console stayed **Healthy through the entire teardown +
  rebuild**.
- Earlier in the session, before the durable fix, the trap required a one-time manual
  `manager-identity unset` to unblock (now automated on teardown).

## Next — console substrate-awareness (ADR-0015 phase-2, `kube_status` provider)

The clear next milestone. The console's architecture page still derives app-tier health from
`docker_status` (the Docker socket), which is **blind to kind pods** — so with the app tier now
in-cluster, that page paints orders-api/orders-db DOWN even though they're Healthy. (The
orders/tracking pages work fine — they hit orders-api + orders-db via the repointed host ports;
only the live topology view is affected.) Scope:

1. Refactor `apps/platform/console/python/app/services/docker_status.py` into a `StatusProvider`
   interface (`poll()` + `subscribe()`), keeping the existing Docker implementation.
2. Add a `KubeStatusProvider` that reads the kind kubeconfig (the one Headlamp already uses) for
   pod/node health in the `orders` + platform namespaces.
3. Select the provider at startup from the injected run-mode descriptor (`substrate: compose|kind`
   — the ADR-0015 phase-1 seam already exists).
4. Same swap for **log streaming** (Docker logs → pod logs).
5. Optional follow-on: ADR-0015 phase-3 topology-as-data (per-domain descriptor, multi-domain).

This closes the only known gap from the app-tier move; the boot-resilience landed in this
checkpoint (`db.py`) is a prerequisite that's already done.

## Other open / follow-ups

- **Alerting on kind** (Apps Progressing>N, worker backlog) — defined in the runbook, not wired
  (kind observability is still an open item from 0009/0011).
- `temporal-worker-controller-crds` can flash `Degraded` transiently during a fresh `platform-up`
  (CRDs being established via ServerSideApply before the controller/webhook settle); it clears to
  Synced/Healthy once established (confirmed) — a transient reconcile artifact, not a standing issue.
- Registry accumulates orders-workers chart versions (0.1.0→0.1.4 this session) + many worker
  build-IDs from dirty-tree rebuilds; harmless locally, but a clean-tag discipline keeps it tidy.
