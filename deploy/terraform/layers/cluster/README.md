# layers/cluster — kind + ArgoCD (STUB, not built yet)

Out of scope for the current checkpoint. This layer will own the local **control plane
on the cluster side**:

- the kind cluster (`deploy/terraform/kind-config.yaml` already has the port maps),
- the ArgoCD install + the root app-of-apps bootstrap,
- per-env Kubernetes namespaces (`orders-nonprod`, `orders-prod`),
- the **Temporal Cloud API key as a k8s Secret** — the credential handoff. This layer
  reads the [cloud layer](../cloud/README.md) outputs (`terraform_remote_state` or a
  piped `terraform output -json`) and materializes the Secret the worker chart expects
  (`deploy/charts/orders-workers/values.yaml` `connection.apiKeySecret`).

The **Terraform↔ArgoCD boundary** sits here: Terraform provisions the cluster, installs
ArgoCD, and lands the credential Secret; everything *running on* the cluster is owned by
ArgoCD (see [workloads](../workloads/README.md)).

Today's `deploy/terraform/main.tf` (kind + ArgoCD) is the seed for this layer and stays
in place until this layer is built out.
