# Cluster layer — the on-cluster control plane: installs ArgoCD, the orders k8s
# namespace, and the Temporal Cloud API-key Secret (the credential handoff). The
# kind cluster itself is CLI-owned (deploy/kind/cluster-up.sh) — this layer only
# talks to its kubeconfig, so the kubernetes/helm providers never depend on a
# not-yet-created resource (the classic kind-TF-provider trap).
#
# Reads the cloud layer's outputs via terraform_remote_state (the `terraform`
# builtin data source — no extra provider needed).

terraform {
  required_version = ">= 1.6"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
    # Applies the ArgoCD Application CRs. Unlike kubernetes_manifest it does not
    # validate against the cluster schema at plan time, so it can create CRs whose
    # CRD (installed by the argo-cd release in the same apply) doesn't exist yet.
    kubectl = {
      source  = "alekc/kubectl"
      version = "~> 2.1"
    }
    # Self-signed cert for the in-cluster TLS proxy that fronts the HTTP registry
    # (so ArgoCD's repo-server can pull OCI charts over HTTPS).
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}
