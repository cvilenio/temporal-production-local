# 0001 — Polyglot + kind/ArgoCD restructure

- **Status:** Landed + committed (`28fda28`). The layout's open questions below were resolved
  by dialogue and the tree was restructured — see **checkpoint 0002** for the final
  `apps/` + `libs/` shape (this file's paths reflect the pre-restructure state).
- **Date:** 2026-06-23

## Done this session

- **Cleanup:** removed leaked `Untitled` Cloud JWT; kept `guts.<account-id>.txt` (real Cloud key,
  gitignored); tightened `.gitignore` (caches, TF state, key/pem/apikey).
- **Shared-kernel migration (43 git renames, history preserved):**
  `kernels/python/orders/` (workflows, activities, clients, db, shared, services,
  `config.py`, `containers.py`, `resources.py`, `api.py`, `worker.py`). uv workspace package.
  Thin apps under `apps/`: `workers/python/{workflow,activity}`, `orders-api`, `codec-server`
  (new), `console`, `mock-api`.
- **Worker fleet:** `WORKER_PROFILES` registry in the kernel — add a profile + a thin app dir
  to add a worker (e.g. CPU- vs IO-bound). Multi-worker-per-language is additive.
- **Worker versioning:** kernel reads `TEMPORAL_DEPLOYMENT_NAME`/`TEMPORAL_WORKER_BUILD_ID` →
  `WorkerDeploymentConfig` (off locally). API confirmed present in pinned `temporalio`.
- **Cloud switch:** `Settings` + `TemporalService.connect()` now do TLS / API-key / mTLS.
  Profiles in `config/`.
- **Build:** one configurable `images/app.Dockerfile`; `docker-compose.yml` rewired (17
  services, codec-server added).
- **Docs:** `docs/ARCHITECTURE.md` + ADRs 0001–0006.
- **Deploy scaffold:** `deploy/{terraform,argocd,charts}` — kind-config + Worker Controller
  CRD chart concrete; rest skeleton with TODOs (see `deploy/README.md`).

## Decisions (see docs/adr/)

TF control plane + ArgoCD/Helm (0002) · self-hosted Temporal on kind + Cloud switch (0003) ·
shared-kernel polyglot layout (0001) · Worker Controller versioning (0004) · connection
profiles (0005) · codec server scaffold (0006).

## Open questions (under review now)

1. **`images/app.Dockerfile` is Python-shaped** — won't hold for Go/TS/Java. Move to
   per-language `images/<lang>/`?
2. **apps/ doesn't surface the three deployment classes** the platform needs:
   (a) Temporal-specific (workers; the SDK client?), (b) Temporal-adjacent (codec server),
   (c) Temporal-independent (console, mock-api — not required to run Temporal in prod).
   Regroup `apps/` by class?
3. **Where does `orders-api` (Temporal SDK client) belong** — class (a) or its own bucket?

## Next

- Resolve the three questions above, then restructure `apps/` + `images/` accordingly and
  re-verify. Then: finish Terraform/ArgoCD wiring, write remaining app Helm charts, real AEAD
  codec, mark `OrderWorkflow` PINNED + wire data-converter codec. Offer a commit.
