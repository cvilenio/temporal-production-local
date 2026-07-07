# The ArgoCD Applications this layer seeds: the committed, secret-free platform
# add-ons (cert-manager, worker-controller[-crds]) plus the injected orders-workers
# Application. The add-ons are delivered the pure-GitOps way; orders-workers carries
# the account-bearing namespace handle + endpoint, which must not live in git
# (see .githooks/pre-commit), so it is seeded here from cloud state. ArgoCD still
# reconciles all of them; git never sees the account id.

locals {
  apps_dir = "${path.module}/../../../argocd/applications"

  # Domain descriptors (config/domains/*.yaml) — consumed at deploy time to inject
  # contract env vars (e.g. TEMPORAL_DATA_CONVERTER) into charts; not read at runtime
  # from worker images (ADR-0026).
  domain_descriptors_dir = "${path.module}/../../../../config/domains"
  ziggymart_descriptor   = yamldecode(file("${local.domain_descriptors_dir}/ziggymart.yaml"))
  ziggymart_data_converter = try(local.ziggymart_descriptor.data_converter, "default")

  # Secret-free platform add-on Applications (cert-manager, worker-controller[-crds])
  # are defined declaratively as committed YAML under deploy/argocd/applications/ —
  # but SEEDED here (not read from git by a root app-of-apps), so startup has no
  # GitHub dependency. They point at the local OCI mirror; their targetRevision is
  # injected here from config/dependencies.yaml (keyed by chart name) so the chart
  # version lives in exactly one place (shared with mirror-deps via deps.env).
  # Backend-specific Prometheus scrape wiring (kept OUT of the committed
  # prometheus.yaml so it stays secret-free + backend-neutral). Cloud scrapes the
  # OpenMetrics endpoint with a Bearer token (honor_timestamps; never rate()); OSS
  # scrapes the in-cluster server's raw :9090 per-service endpoints (annotation
  # discovery scoped to the temporal namespace, with a stable job=temporal-oss label
  # the self-hosted-internals dashboards key on). $1/$2 are Prometheus relabel refs
  # (literal to Terraform — only $${ } would interpolate).
  scrape_oss = <<-EOT
    - job_name: temporal-oss
      kubernetes_sd_configs:
        - role: pod
          namespaces:
            names: ['${var.temporal_k8s_namespace}']
      relabel_configs:
        - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
          action: keep
          regex: "true"
        - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
          action: replace
          regex: ([^:]+)(?::\d+)?;(\d+)
          replacement: $1:$2
          target_label: __address__
        - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name]
          target_label: temporal_service
  EOT

  scrape_cloud = <<-EOT
    - job_name: temporal-cloud
      scheme: https
      metrics_path: /v1/metrics
      honor_timestamps: true
      scrape_interval: 30s
      scrape_timeout: 10s
      authorization:
        type: Bearer
        credentials_file: /etc/secrets/cloud/api-key
      static_configs:
        - targets: ['metrics.temporal.io']
  EOT

  prometheus_scrape_configs = local.is_oss ? local.scrape_oss : local.scrape_cloud

  # Cloud mounts the OpenMetrics bearer-token Secret by file; OSS needs no mount.
  prometheus_server_extra = local.is_oss ? {} : {
    extraSecretMounts = [{
      name       = "cloud-metrics-apikey"
      secretName = "cloud-metrics-apikey"
      mountPath  = "/etc/secrets/cloud"
      readOnly   = true
    }]
  }

  addon_applications = [
    for a in [for f in fileset(local.apps_dir, "*.yaml") : yamldecode(file("${local.apps_dir}/${f}"))] :
    merge(a, {
      spec = merge(a.spec, {
        source = merge(
          a.spec.source,
          { targetRevision = local.chart_versions[a.spec.source.chart] },
          # Inject the backend-specific scrape wiring into the prometheus app only.
          a.metadata.name != "prometheus" ? {} : {
            helm = merge(a.spec.source.helm, {
              valuesObject = merge(a.spec.source.helm.valuesObject, {
                extraScrapeConfigs = local.prometheus_scrape_configs
                server             = merge(a.spec.source.helm.valuesObject.server, local.prometheus_server_extra)
              })
            })
          },
        )
      })
    })
  ]

  # Worker images: pinned by digest when `just ci` supplied one (immutable,
  # content-addressed Build ID), else by tag. Image bytes live in the local
  # registry; in-cluster the nodes pull them as localhost:5001/... via certs.d.
  worker_image = {
    workflow      = { repository = "localhost:5001/orders-worker-workflow", tag = var.worker_image_tag, digest = lookup(var.worker_image_digests, "workflow", "") }
    activity      = { repository = "localhost:5001/orders-worker-activity", tag = var.worker_image_tag, digest = lookup(var.worker_image_digests, "activity", "") }
    activity_java = { repository = "localhost:5001/orders-worker-activity-java", tag = var.worker_image_tag, digest = lookup(var.worker_image_digests, "activity-java", "") }
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
              tls               = true                       # ON in both modes; credential type differs
              apiKeySecret      = local.worker_apikey_secret # Cloud: set; OSS: ""
              mtlsSecret        = local.worker_mtls_secret   # OSS: set; Cloud: ""
            }
            # Turn on per-worker autoscaling (ADR-0023): renders the WorkerAutoscaler
            # CRs consumed by the temporal-worker-autoscaler controller. Only the
            # kind+Cloud path enables it; the host/OSS `helm template` path keeps the
            # chart default (enabled: false). Deep-merges, so only `enabled` flips.
            autoscaling = { enabled = true }
            dataConverter = local.ziggymart_data_converter
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
              {
                name           = "activity-java"
                deploymentName = "orders-activity-java"
                replicas       = 1
                language       = "java"
                image          = local.worker_image.activity_java
                startupProbe = {
                  type = "httpGet"
                  path = "/health/readiness"
                  port = 9000
                }
                extraEnv = {
                  OTEL_SERVICE_NAME = "orders-worker-activity-java"
                  SDK_METRICS_PORT  = "9000"
                }
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
              apiKeySecret      = local.client_apikey_secret # Cloud: set; OSS: ""
              mtlsSecret        = local.client_mtls_secret   # OSS: set; Cloud: ""
            }
            dataConverter = local.ziggymart_data_converter
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
              apiKeySecret      = local.worker_apikey_secret   # Cloud: set; OSS: ""
              mtlsSecret        = local.autoscaler_mtls_secret # OSS: set; Cloud: ""
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

  # temporal-server: the in-cluster OSS backend (ADR-0003), a LOCAL wrapper chart
  # (published by `just chart-publish`) over the official Temporal chart + CNPG
  # Postgres + cert-manager mTLS + a namespace/search-attribute bootstrap Job.
  #
  # releaseName = "temporal" is REQUIRED: the upstream subchart names its Services
  # <release>-<role>, so this forces temporal-frontend / temporal-web / etc. — the
  # names the mTLS SANs, the workers' hostPort, and the console selectors all assume.
  #
  # Its EXISTENCE is gated on var.oss_server_enabled, NOT temporal_backend (decoupled
  # lifecycle): switching workers back to Cloud leaves the server running so its state
  # survives; `just temporal-server-down` sets oss_server_enabled=false to remove it.
  # sync-wave -1: after the CNPG operator + cert-manager add-ons (wave -2), before the
  # workers (wave 0).
  temporal_server_application = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "temporal-server"
      namespace   = var.argocd_namespace
      annotations = { "argocd.argoproj.io/sync-wave" = "-1" }
    }
    spec = {
      project = "default"
      source = {
        repoURL        = var.oci_charts_repo
        chart          = "temporal-server"
        targetRevision = var.temporal_server_chart_version
        helm = {
          releaseName = "temporal"
          valuesObject = {
            namespaceName   = var.temporal_k8s_namespace
            ordersNamespace = var.orders_namespace
          }
        }
      }
      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = var.temporal_k8s_namespace
      }
      syncPolicy = {
        automated = { prune = true, selfHeal = true }
        # ServerSideApply: the official chart's server ConfigMap is large; SSA avoids
        # the client-side last-applied annotation size limit. CreateNamespace for the
        # temporal namespace.
        syncOptions = ["CreateNamespace=true", "ServerSideApply=true"]
      }
    }
  }

  # Every Application TF seeds: the committed add-ons + the injected orders-workers,
  # orders-data, orders-api, the autoscaler controller, the alloy log agent, and
  # (only when oss_server_enabled) the in-cluster OSS temporal-server.
  all_applications = { for app in concat(
    local.addon_applications,
    [local.orders_workers_application, local.orders_data_application, local.orders_api_application, local.temporal_worker_autoscaler_application, local.alloy_application],
    var.oss_server_enabled ? [local.temporal_server_application] : [],
  ) : app.metadata.name => app }
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
    # One-directional guard: running the workers/apps against OSS requires the OSS
    # server to exist (it issues the mTLS client-cert Secrets they mount). The reverse
    # is intentionally free — oss_server_enabled=true with backend=cloud is the valid
    # "server up but idle" state that keeps switch-backend non-destructive. Without
    # this, an off-`just` apply of temporal_backend=oss with the default
    # oss_server_enabled=false strands every worker pod in ContainerCreating (the
    # temporal-worker-mtls Secret never gets issued).
    precondition {
      condition     = !local.is_oss || var.oss_server_enabled
      error_message = "temporal_backend=\"oss\" requires oss_server_enabled=true (the OSS server issues the mTLS client certs the workers/apps mount). Use `just platform-up oss`, which sets both."
    }
  }
}
