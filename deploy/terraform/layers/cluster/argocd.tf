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

  # - server.insecure: serve the UI/API over plain HTTP locally. Not for prod.
  # - server.service: expose the UI on a fixed NodePort (30808) which the
  #   kind config maps to host :8090. The host-plane viz-proxy (Compose)
  #   fronts that and strips frame headers so the demo console can iframe it
  #   (ADR-0014). Replaces the old `port-forward svc/argocd-server` hint.
  # - repositories.local-charts: the in-cluster OCI registry, plain-HTTP + insecure,
  #   so ArgoCD pulls every chart locally (no GitHub/public-internet).
  values = [
    yamlencode({
      configs = {
        params = {
          "server.insecure" = true
        }
        # Zero-friction local viewing: anonymous read-only access so the demo
        # console can frame the UI without a login wall (ADR-0014). NON-PROD —
        # never expose ArgoCD this way in a customer environment.
        cm = {
          "users.anonymous.enabled" = "true"
        }
        rbac = {
          "policy.default" = "role:readonly"
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
      server = {
        service = {
          type         = "NodePort"
          nodePortHttp = 30808
        }
      }
    })
  ]

  depends_on = [kubernetes_secret.orders_cloud_apikey]
}
