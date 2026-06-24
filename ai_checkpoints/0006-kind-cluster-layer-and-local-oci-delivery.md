# 0006 — kind cluster layer + production-grade local-only OCI delivery

- **Status:** **LANDED in working tree (2026-06-24), not yet committed.** Verified end-to-end live:
  kind up, ArgoCD pulling all charts from the local registry (no GitHub/internet), cert-manager +
  Worker Controller running from the local mirror, digest-pinned workers registered on Temporal
  Cloud `ziggymart-nonprod`.
- **Date:** 2026-06-24

## Context

Checkpoint 0005 left `layers/cluster` (kind + ArgoCD + Cloud-key Secret) as the next dependency
gating the kind run-modes. This session built it for the **kind + Cloud** run-mode, then hardened
delivery to be **local-only / air-gap-capable** per the user's requirements.

## Done this session

### Cluster layer (kind + Cloud), ADR-0009
- kind lifecycle is **CLI-owned** (`deploy/kind/cluster-up.sh` / `cluster-down.sh`, via `just`),
  kubeconfig under `.secrets/kube/`. Terraform's kubernetes/helm providers read that kubeconfig —
  no kind-provider "config depends on a not-yet-created resource" trap. Removed the legacy seed
  (`deploy/terraform/main.tf` + `variables.tf` + `versions.tf`).
- `deploy/terraform/layers/cluster/`: ArgoCD (helm_release), `orders` namespace, Cloud worker
  **API-key Secret** read from the cloud layer's state via `terraform_remote_state`. Confirmed the
  cloud `endpoint` output is already the **regional** endpoint (API keys reject the namespace
  endpoint); namespaces are `api_key_auth=true` (mTLS not an option).
- orders-workers chart fixed to the real Worker Controller CRD (`apiKeySecretRef{name,key}`,
  `workerOptions.connectionRef.name`); pod template sets `TEMPORAL_TLS=true` (controller injects the
  key but not TLS). Worker Controller pinned **chart 0.26.0 / app 1.7.0**.

### Local-only OCI delivery, ADR-0011
- Local OCI registry is **zot** (CNCF, OCI-native): hosts our pushes AND **pull-through-caches**
  upstream images on demand (`deploy/kind/zot-config.json` `sync` extension, prefix-routed). Node
  `certs.d` redirects quay.io/registry.k8s.io/docker.io → zot (cache miss → fetch+cache; upstream
  fallback for un-enumerated images). `just mirror-deps` now pushes only the third-party **charts**
  (cert-manager classic-helm + Worker Controller OCI → `oci://localhost:5001/charts`); images are no
  longer copied (zot caches them). `just chart-publish` publishes orders-workers as an OCI chart.
  (Replaced the earlier `crane copy` explicit image mirror.)
- **All Applications TF-seeded** (`kubectl_manifest`) — no git-backed app-of-apps, no GitHub
  dependency at startup. Add-on Application defs are committed YAML, inlined by TF; orders-workers
  values (account-bearing handle/endpoint/digests) injected from cloud state, never committed.
- **TLS proxy** (`registry-proxy.tf`, nginx) fronts the HTTP registry for ArgoCD's repo-server
  (ArgoCD v3 `insecure` = skip-verify, not plain-http). Every other client uses the registry over
  HTTP unchanged. `git push origin main` is unchanged; ArgoCD just doesn't read git.

### Disk hygiene + single version source (ADR-0011 refinements)
- zot `dedupe` + scheduled GC (`gcInterval`/`gcDelay`) + `retention` policies (keep N most-recent
  tags + `pulledWithin` window, delete untagged) so cache/pushes self-prune. Manual reset:
  `docker volume rm kind-registry-data`.
- **`config/dependencies.yaml`** = single source for third-party versions (cert-manager, Worker
  Controller, argo-cd, zot, nginx). Read by Terraform (`yamldecode` → injects Application
  `targetRevision` + argo-cd version + proxy image) and by the bash scripts (rendered to
  `config/.generated/deps.env` by `compose/scripts/render-deps.py`). Killed the cert-manager/
  worker-controller version duplication. `terraform plan` after the refactor = **No changes**.

### Air-gap boundary (ADR-0013) + offline resume
- ADR-0013 sets the principle: **air-gap artifacts (images+charts), not source indexes**
  (PyPI/npm/Go/Maven). Boundary = the OCI registry. Three-tier offline contract: Run (guaranteed) /
  Rebuild (best-effort) / Resolve (needs network). RUNMODES has an "Offline contract" section.
- `just cluster-stop` / `cluster-start` (docker stop/start nodes + registry): a **stopped** cluster
  resumes fully offline (node containerd cache + zot `kind-registry-data` volume persist); a deleted
  one cannot (tier-3 bootstrap). Flight workflow: `platform-up` online → `cluster-stop` → fly →
  `cluster-start`. Verified: stop/start cycle resumes pods (preserved, not recreated) + apps
  Synced/Healthy with no pulls.

### Tooling + versioning, ADR-0010 / ADR-0012
- **`just`** front door (Rust task runner) delegating to `poe` (Python) and infra scripts;
  `just platform-up` orchestrates the whole bring-up.
- Images tagged `git describe --always --dirty`; **deployed by digest** (`repository@sha256`), so
  the Worker Controller Build ID is content-addressed. `poe ci` = lint→test→build→push.
- Branch/promotion model documented: feature → main(nonprod) → immutable tag(prod); two anti-thrash
  layers (GitOps discipline + Temporal PINNED versioning).
- App fix surfaced by enabling versioning: `OrderWorkflow` now declares
  `versioning_behavior=VersioningBehavior.PINNED` (order processing completes in minutes; ADR-0004).

## Verified live
- `just cluster-up` (3 nodes), registry round-trip (in-cluster pod pulled a `localhost:5001` image).
- zot pull-through verified: an in-cluster pod pulled a not-yet-cached `quay.io/jetstack/...` image
  → zot fetched it from upstream and cached it (now in the catalog); in-cluster Service reachable.
  `just mirror-deps` pushes the third-party charts; `just chart-publish` the orders-workers chart.
- `terraform apply` → ArgoCD + 4 Applications **Synced/Healthy**, all charts pulled from local OCI.
- Workers Running, digest-pinned; **both worker deployments registered on Cloud `ziggymart-nonprod`**
  (`temporal worker deployment list` over the regional endpoint + API key).
- `terraform fmt`/`validate`, `helm lint`, `ruff`/`poe lint` clean.

## Notes / open items
- ArgoCD itself (chart via TF `helm_release`, images from quay.io) is the bootstrap tool and is
  **not** mirrored — the remaining step for strict air-gap (also drop the `certs.d` upstream
  fallback). Noted in ADR-0011.
- Cloud worker deployment versions register **Inactive** (rollout strategy `Manual`) — promote via
  the Temporal UI/CLI to make a version Current.
- Installed via brew this session: `just`, `kind`, `helm`, `crane`. Fixed a pre-existing stale-venv
  breakage (repo rename) and a pre-existing format nit in `render-oss-bootstrap.py`.

## Next
- Replay/NDE test gate (deferred): recorded histories through the SDK `Replayer` in `poe ci`.
- kind + **OSS** run-mode (Workstreams B/C): self-hosted temporal-server + CNPG + OSS auth, reusing
  this layer's sync-wave + mirror patterns.
- Optional: thin GitHub Actions wrapper calling `poe ci`; mirror argocd for strict air-gap.
