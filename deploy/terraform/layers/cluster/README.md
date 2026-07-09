# layers/cluster — on-cluster control plane (kind + ArgoCD + credential handoff)

Owns the local **control plane on the cluster side**:

- **ArgoCD** install (the only imperatively-installed component) + the GitOps
  bootstrap (root app-of-apps seeded via the chart's `extraObjects`).
- the **orders** Kubernetes namespace.
- the **Temporal Cloud worker API-key Secret** (`orders-cloud-apikey`) — the
  credential handoff. This layer reads the [cloud layer](../cloud/README.md)
  outputs via `terraform_remote_state` and materializes the Secret the
  orders-workers chart consumes (`apiKeySecretRef`).

The kind cluster itself is **CLI-owned** (`deploy/kind/cluster-up.sh`, run via
`just kind-up`), not the Terraform kind provider — so the kubernetes/helm
providers read a real kubeconfig instead of depending on a not-yet-created
resource. See the cluster-seam ADR.

## The Terraform↔ArgoCD boundary

Terraform lands the cluster prerequisites (ArgoCD, namespace, credential Secret),
the TLS proxy that fronts the registry, and **seeds all Applications**
(`kubectl_manifest`); everything that *runs on* the cluster is reconciled by
ArgoCD (ADR-0002). Delivery is **local-only** (ADR-0011): ArgoCD pulls every chart
from the local OCI registry — no GitHub/public-internet. The add-on Application
definitions are committed YAML ([`deploy/argocd/applications/`](../../../argocd/applications/),
read + inlined by TF); the **orders-workers** Application is injected here with the
account-bearing namespace handle + regional endpoint + image digests from cloud
state, so the account id never lands in git (`.githooks/pre-commit`).

Files: `main.tf` (ArgoCD, namespace, Secret, seeded Apps), `registry-proxy.tf`
(TLS proxy so ArgoCD can pull OCI charts over HTTPS), `remote-state.tf` (reads the
cloud layer).

## Usage

```sh
just platform-up   # full cold start (host + cluster): recommended
just cluster-up    # kind side only (host must already be up via host-up): mirror deps,
                   # CI (build/push), publish chart, pin digests, terraform apply.

# Reach the ArgoCD UI (plain HTTP, local only):
just k -n argocd port-forward svc/argocd-server 8080:80

# Going offline (e.g. a flight): stop (don't delete), then resume with no network.
just cluster-stop        # before you lose connectivity
just cluster-start       # offline — workers + apps resume from cache (ADR-0013)
```

Requires the [cloud layer](../cloud/README.md) to have been applied first (its
state is the source of the API key, endpoint, and namespace handle).

> **Delivery note:** ArgoCD pulls charts from the local registry, not git. Local
> edits reconcile after `just chart-publish` (orders-workers) / `just mirror-deps`
> (third-party) and a re-apply — not after a git push. Your `git push origin main`
> is unchanged. See ADR-0011.
