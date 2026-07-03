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

  # orders-api image: same digest-or-tag pinning as the workers.
  orders_api_image = { repository = "localhost:5001/orders-api", tag = var.orders_api_image_tag, digest = var.orders_api_image_digest }

  # temporal-worker-autoscaler controller image: same digest-or-tag pinning.
  autoscaler_image = { repository = "localhost:5001/temporal-worker-autoscaler", tag = var.autoscaler_image_tag, digest = var.autoscaler_image_digest }

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
            # Turn on per-worker autoscaling (ADR-0023): renders the WorkerAutoscaler
            # CRs consumed by the temporal-worker-autoscaler controller. Only the
            # kind+Cloud path enables it; the host/OSS `helm template` path keeps the
            # chart default (enabled: false). Deep-merges, so only `enabled` flips.
            autoscaling = { enabled = true }
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

  # orders-data: the CNPG orders-db Cluster + its git-safe credential. Its OWN
  # Application (separate failure domain) so a slow/failed DB bootstrap can never
  # stall the orders-api tier's sync (ADR-0016). No account-bearing values — the
  # chart defaults are git-safe — so no valuesObject injection.
  orders_data_application = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "orders-data"
      namespace   = var.argocd_namespace
      annotations = { "argocd.argoproj.io/sync-wave" = "0" }
    }
    spec = {
      project = "default"
      source = {
        repoURL        = var.oci_charts_repo
        chart          = "orders-data"
        targetRevision = var.orders_data_chart_version
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

  # orders-api: the Temporal client Deployment + Service. Its OWN Application,
  # authenticated to Cloud as the dedicated CLIENT identity (orders-client-apikey).
  # Depends on orders-db at RUNTIME via k8s readiness, NOT an ArgoCD sync gate
  # (ADR-0016) — so it crash-loops-until-ready rather than deadlocking.
  orders_api_application = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "orders-api"
      namespace   = var.argocd_namespace
      annotations = { "argocd.argoproj.io/sync-wave" = "0" }
    }
    spec = {
      project = "default"
      source = {
        repoURL        = var.oci_charts_repo
        chart          = "orders-api"
        targetRevision = var.orders_api_chart_version
        helm = {
          valuesObject = {
            ordersApi = {
              image = local.orders_api_image
            }
            connection = {
              hostPort          = local.temporal_address
              temporalNamespace = local.namespace_handle
              tls               = true
              apiKeySecret      = var.client_apikey_secret_name
            }
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

  # alloy: the Grafana Alloy log-collection DaemonSet (ADR-0018). A LOCAL chart
  # (deploy/charts/alloy, published by `just chart-publish` like the orders charts),
  # seeded here rather than via the addon glob since its version comes from the
  # chart, not config/dependencies.yaml. No account-bearing values — the agent
  # ships to the host backend, not Temporal Cloud. sync-wave -1: collection is up
  # before the app workloads (wave 0) start producing logs.
  alloy_application = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "alloy"
      namespace   = var.argocd_namespace
      annotations = { "argocd.argoproj.io/sync-wave" = "-1" }
    }
    spec = {
      project = "default"
      source = {
        repoURL        = var.oci_charts_repo
        chart          = "alloy"
        targetRevision = var.alloy_chart_version
        # Committed log pipeline (ADR-0020): the agent ships pod logs OTLP to the
        # host-side OTel Collector → ClickHouse. Not account-bearing.
        helm = {
          valuesObject = {
            clickhouse = {
              otlpUrl = var.alloy_clickhouse_otlp_url
            }
          }
        }
      }
      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = "observability"
      }
      syncPolicy = {
        automated   = { prune = true, selfHeal = true }
        syncOptions = ["CreateNamespace=true"]
      }
    }
  }

  # temporal-worker-autoscaler: the custom worker autoscaling controller (ADR-0023),
  # a LOCAL chart published by `just chart-publish`. Account-bearing connection +
  # controller image injected here from cloud state / `just ci`. Deployed to the
  # orders namespace (co-located with the Cloud API-key Secret + the worker
  # Deployments it patches). sync-wave 0, after the wave -2/-1 add-ons and alongside
  # the workers it scales.
  temporal_worker_autoscaler_application = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "temporal-worker-autoscaler"
      namespace   = var.argocd_namespace
      annotations = { "argocd.argoproj.io/sync-wave" = "0" }
    }
    spec = {
      project = "default"
      source = {
        repoURL        = var.oci_charts_repo
        chart          = "temporal-worker-autoscaler"
        targetRevision = var.autoscaler_chart_version
        helm = {
          valuesObject = {
            connection = {
              hostPort          = local.temporal_address
              temporalNamespace = local.namespace_handle
              tls               = true
              apiKeySecret      = var.cloud_apikey_secret_name
            }
            image = local.autoscaler_image
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

  # Every Application TF seeds: the committed add-ons + the injected orders-workers,
  # orders-data, orders-api, the autoscaler controller, and the alloy log agent.
  all_applications = { for app in concat(local.addon_applications, [local.orders_workers_application, local.orders_data_application, local.orders_api_application, local.temporal_worker_autoscaler_application, local.alloy_application]) : app.metadata.name => app }
}

# Seed the ArgoCD Applications after the release installs the Application CRD.
# kubectl_manifest defers schema validation to apply time, so this works in a
# single `terraform apply` despite the CRD being created in the same run.
resource "kubectl_manifest" "applications" {
  for_each  = local.all_applications
  yaml_body = yamlencode(each.value)

  # After ArgoCD (Application CRD) and the TLS proxy ArgoCD pulls charts through.
  depends_on = [helm_release.argocd, kubernetes_deployment.registry_proxy]

  # Guard the silent-`:latest` footgun: with no digest, the chart falls back to
  # `:{tag}`, and tag defaults to "latest" — which isn't in the local registry,
  # so workers land in ImagePullBackOff while ArgoCD still reports the CR healthy.
  # A bare `terraform apply` (without the digest var `just platform-up` computes)
  # used to hit exactly this. Fail loudly instead. A real (non-"latest") tag that
  # exists in the registry is still a valid digest-free fallback.
  lifecycle {
    precondition {
      condition = alltrue([
        for img in concat(values(local.worker_image), [local.orders_api_image, local.autoscaler_image]) :
        img.digest != "" || (img.tag != "" && img.tag != "latest")
      ])
      error_message = "Unsafe image ref: each worker AND orders-api needs a pinned digest (preferred) or a non-'latest' tag that exists in the local registry. Empty digest + tag='latest' silently deploys :latest and breaks the pod. Use `just platform-up` (it builds + computes digests) rather than a bare `terraform apply`."
    }
  }
}
