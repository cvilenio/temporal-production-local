# ArgoCD, installed imperatively — the only thing not delivered by ArgoCD itself.
# It is the delivery tool plus the GitOps bootstrap; everything that runs on the
# cluster is then reconciled by it (ADR-0002). The Applications it reconciles are
# seeded separately (see applications.tf) so they apply AFTER this release installs
# the Application CRD.
resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = local.deps.charts["argo-cd"].repo
  chart            = "argo-cd"
  version          = local.chart_versions["argo-cd"]
  namespace        = var.argocd_namespace
  create_namespace = true

  # - server.insecure: serve the UI/API over plain HTTP locally (reach it with
  #   `just k -n argocd port-forward svc/argocd-server 8080:80`). Not for prod.
  # - repositories.local-charts: the in-cluster OCI registry, plain-HTTP + insecure,
  #   so ArgoCD pulls every chart locally (no GitHub/public-internet).
  values = [
    yamlencode({
      configs = {
        params = {
          "server.insecure" = true
        }
        repositories = {
          local-charts = {
            name      = "local-charts"
            url       = var.oci_charts_repo
            type      = "helm"
            enableOCI = "true"
            insecure  = "true"
          }
        }
      }
    })
  ]

  depends_on = [kubernetes_secret.orders_cloud_apikey]
}
