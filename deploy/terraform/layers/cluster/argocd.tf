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
          # Custom health for the Worker Controller CRD. Without this, ArgoCD has
          # no health logic for temporal.io/WorkerDeployment and defaults unknown
          # CRDs to Healthy — so the app showed green even while the controller's
          # pods were in ImagePullBackOff (the operator/GitOps visibility gap:
          # ArgoCD tracks the CR it applied, not the pods the controller spawns).
          # We read the CR's own conditions instead. NOTE: `WaitingForPromotion`
          # is the intended Manual-rollout hold (ADR-0012), so it maps to Healthy;
          # a stuck/not-yet-polling rollout maps to Progressing/Degraded.
          "resource.customizations.health.temporal.io_WorkerDeployment" = <<-EOT
            local hs = {}
            if obj.status ~= nil and obj.status.conditions ~= nil then
              local ready = nil
              local progressing = nil
              for i, c in ipairs(obj.status.conditions) do
                if c.type == "Ready" then ready = c end
                if c.type == "Progressing" then progressing = c end
              end
              if ready ~= nil and ready.status == "True" then
                hs.status = "Healthy"
                hs.message = ready.message
                return hs
              end
              if (ready ~= nil and ready.reason == "WaitingForPromotion") or
                 (progressing ~= nil and progressing.reason == "WaitingForPromotion") then
                hs.status = "Healthy"
                hs.message = "Waiting for manual promotion (rollout strategy Manual)"
                return hs
              end
              if progressing ~= nil and progressing.status == "True" then
                hs.status = "Progressing"
                hs.message = progressing.message
                return hs
              end
              if ready ~= nil then
                hs.status = "Degraded"
                hs.message = ready.message
                return hs
              end
            end
            hs.status = "Progressing"
            hs.message = "Waiting for WorkerDeployment status"
            return hs
          EOT
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
