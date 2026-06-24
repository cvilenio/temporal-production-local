# ADR-0009: layers/cluster — kind CLI-owned, kubeconfig-fed Terraform, credential handoff

- **Status:** Accepted. **Delivery refined by [ADR-0011](0011-local-oci-delivery.md):** the
  git-backed chart/app-of-apps delivery described below was replaced with local-OCI delivery
  (ArgoCD pulls all charts from the local registry; all Applications are TF-seeded). The cluster
  seam, kind-CLI ownership, and credential-handoff decisions here still stand.
- **Date:** 2026-06-24

## Context

ADR-0002 split the platform into a Terraform control plane and an ArgoCD/Helm workload plane,
and named `layers/cluster` as the on-cluster control plane (kind + ArgoCD + the Cloud API-key
Secret). Building it surfaced three concrete decisions the earlier ADR left open, plus a hard
constraint from the secrets policy.

The seed `deploy/terraform/main.tf` used the `tehcyx/kind` provider and configured the
kubernetes/helm providers from the `kind_cluster` resource's attributes. That is the classic
"provider configuration depends on a not-yet-created resource" trap: fragile at `plan`, ordering-
sensitive at `apply`. We also need the worker's Temporal Cloud API key and the account-bearing
namespace handle in the cluster — but the account id must not be committed (`.githooks/pre-commit`,
the deliberate `.secrets/` design). This increment targets **kind + Cloud** (workers on kind →
Temporal Cloud nonprod); self-hosted-on-kind (CNPG, `temporal-server`, sync-waves, OSS auth) stays
a later workstream.

## Decision

1. **kind is CLI-owned, not Terraform-owned.** `deploy/kind/cluster-up.sh` (run via
   `just cluster-up`) creates the cluster and writes its kubeconfig under the hardened `.secrets/`.
   The cluster layer's `kubernetes`/`helm` providers read that kubeconfig (`config_path`), so they
   never depend on a resource created in the same apply. This mirrors how we would treat a
   pre-existing GKE cluster — the cluster is substrate, not Terraform state.

2. **A local OCI registry is wired to kind** (the upstream kind+registry recipe). `just ci` builds
   worker images tagged with the git SHA and pushes to `localhost:5001`; containerd on each node
   resolves it via `certs.d`. This gives real push/pull semantics — image refs and the ArgoCD/
   controller pull path behave exactly as on GKE + Artifact Registry — instead of `kind load`.

3. **The credential handoff is the cluster layer's job, sourced from cloud state.** The layer reads
   the cloud layer's outputs via `terraform_remote_state` and materializes the worker API key as a
   k8s Secret (`orders-cloud-apikey`). Because API keys require the **regional** endpoint and the
   account-bearing namespace handle — neither committable — the layer also **seeds the
   `orders-workers` ArgoCD Application** with those values injected from cloud state. ArgoCD still
   reconciles the workload; git never sees the account id. Secret-free platform add-ons
   (cert-manager, Worker Controller) are delivered the pure-GitOps way via the root app-of-apps
   reading `deploy/argocd/applications/`. This is the same control-plane/data-plane asymmetry as
   ADR-0007: the control plane owns the one secret-bearing piece; everything else is GitOps.

4. **The Temporal Worker Controller is pinned to chart 0.26.0 / appVersion 1.7.0** — the GA line
   that supports `apiKeySecretRef` (1.1.0/1.2.0 are upstream-marked unstable). CRDs install as a
   separate chart so their lifecycle never churns on controller upgrades.

## Consequences

- `terraform plan`/`apply` for the cluster layer is robust (no kind-provider plan fragility); the
  seed `main.tf`/`variables.tf`/`versions.tf` at the terraform root are removed.
- The kind cluster's lifecycle is decoupled from Terraform state — `just cluster-down` does not
  touch tfstate; a recreate just rewrites the kubeconfig.
- One ArgoCD Application (`orders-workers`) is seeded by Terraform rather than committed to
  `deploy/argocd/applications/`. That asymmetry is intentional and documented; the committed
  app-of-apps still expresses the pure-GitOps delivery of the secret-free add-ons.
- Worker code is unchanged: it already reads `TEMPORAL_*` (incl. `TEMPORAL_API_KEY`) and
  `TEMPORAL_WORKER_BUILD_ID`. The chart sets `TEMPORAL_TLS=true` on the pod template because the
  controller injects the API key but not TLS, and API keys require TLS.
- mTLS-to-Cloud was not an option: the namespaces are provisioned `api_key_auth=true` (cert auth
  rejected). If that changes, the chart's `mutualTLSSecretRef` path already exists.
