# Control plane: kind cluster + ArgoCD install + root app-of-apps.
# Temporal Cloud is provisioned in cloud.tf (gated on var.provision_cloud).
#
# SKELETON: blocks are wired but values/TODOs need a pass before first apply.

provider "kind" {}

resource "kind_cluster" "this" {
  name           = var.cluster_name
  wait_for_ready = true
  kind_config    = file("${path.module}/kind-config.yaml")
}

# kube/helm providers target the kind cluster created above.
provider "kubernetes" {
  host                   = kind_cluster.this.endpoint
  client_certificate     = kind_cluster.this.client_certificate
  client_key             = kind_cluster.this.client_key
  cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
}

provider "helm" {
  kubernetes {
    host                   = kind_cluster.this.endpoint
    client_certificate     = kind_cluster.this.client_certificate
    client_key             = kind_cluster.this.client_key
    cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
  }
}

# ArgoCD — the only delivery tool installed imperatively; it owns everything else.
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  namespace        = "argocd"
  create_namespace = true
  # TODO: pin chart version; minimal values (insecure UI for local, ingress).
}

# Bootstrap the app-of-apps so ArgoCD reconciles deploy/argocd/applications/.
# TODO: point repoURL at this repo; for pure-local you can `kubectl apply` it instead.
# resource "kubernetes_manifest" "root_app" {
#   manifest = yamldecode(file("${path.module}/../argocd/root-app.yaml"))
#   depends_on = [helm_release.argocd]
# }

output "kubeconfig_path" {
  value = kind_cluster.this.kubeconfig_path
}
