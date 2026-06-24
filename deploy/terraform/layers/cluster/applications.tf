# The ArgoCD Applications this layer seeds: the committed, secret-free platform
# add-ons (cert-manager, worker-controller[-crds]) plus the injected orders-workers
# Application. The add-ons are delivered the pure-GitOps way; orders-workers carries
# the account-bearing namespace handle + endpoint, which must not live in git
# (see .githooks/pre-commit), so it is seeded here from cloud state. ArgoCD still
# reconciles all of them; git never sees the account id.

locals {
  apps_dir = "${path.module}/../../../argocd/applications"

  # Secret-free platform add-on Applications (cert-manager, worker-controller[-crds])
  # are defined declaratively as committed YAML under deploy/argocd/applications/ —
  # but SEEDED here (not read from git by a root app-of-apps), so startup has no
  # GitHub dependency. They point at the local OCI mirror; their targetRevision is
  # injected here from config/dependencies.yaml (keyed by chart name) so the chart
  # version lives in exactly one place (shared with mirror-deps via deps.env).
  addon_applications = [
    for a in [for f in fileset(local.apps_dir, "*.yaml") : yamldecode(file("${local.apps_dir}/${f}"))] :
    merge(a, {
      spec = merge(a.spec, {
        source = merge(a.spec.source, { targetRevision = local.chart_versions[a.spec.source.chart] })
      })
    })
  ]

  # Worker images: pinned by digest when `just ci` supplied one (immutable,
  # content-addressed Build ID), else by tag. Image bytes live in the local
  # registry; in-cluster the nodes pull them as localhost:5001/... via certs.d.
  worker_image = {
    workflow = { repository = "localhost:5001/orders-worker-workflow", tag = var.worker_image_tag, digest = lookup(var.worker_image_digests, "workflow", "") }
    activity = { repository = "localhost:5001/orders-worker-activity", tag = var.worker_image_tag, digest = lookup(var.worker_image_digests, "activity", "") }
  }

  # orders-workers: chart pulled from the local OCI registry; the account-bearing
  # connection values are injected here from cloud state (never committed to git).
  # sync-wave 0 keeps it after the wave -2/-1 add-ons.
  orders_workers_application = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "orders-workers"
      namespace   = var.argocd_namespace
      annotations = { "argocd.argoproj.io/sync-wave" = "0" }
    }
    spec = {
      project = "default"
      source = {
        repoURL        = var.oci_charts_repo
        chart          = "orders-workers"
        targetRevision = var.orders_workers_chart_version
        helm = {
          valuesObject = {
            connection = {
              hostPort          = local.temporal_address
              temporalNamespace = local.namespace_handle
              tls               = true
              apiKeySecret      = var.cloud_apikey_secret_name
            }
            workers = [
              {
                name           = "workflow"
                deploymentName = "orders-workflow"
                replicas       = 1
                image          = local.worker_image.workflow
                command        = ["python", "main.py"]
              },
              {
                name           = "activity"
                deploymentName = "orders-activity"
                replicas       = 2
                image          = local.worker_image.activity
                command        = ["python", "main.py"]
              },
            ]
          }
        }
      }
      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = var.orders_namespace
      }
      syncPolicy = {
        automated   = { prune = true, selfHeal = true }
        syncOptions = ["CreateNamespace=true"]
      }
    }
  }

  # Every Application TF seeds: the committed add-ons + the injected orders-workers.
  all_applications = { for app in concat(local.addon_applications, [local.orders_workers_application]) : app.metadata.name => app }
}

# Seed the ArgoCD Applications after the release installs the Application CRD.
# kubectl_manifest defers schema validation to apply time, so this works in a
# single `terraform apply` despite the CRD being created in the same run.
resource "kubectl_manifest" "applications" {
  for_each  = local.all_applications
  yaml_body = yamlencode(each.value)

  # After ArgoCD (Application CRD) and the TLS proxy ArgoCD pulls charts through.
  depends_on = [helm_release.argocd, kubernetes_deployment.registry_proxy]
}
