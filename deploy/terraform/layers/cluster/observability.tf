# The observability namespace and the Temporal Cloud metrics credential seeded into
# it (ADR-0021 metrics phase). Same handoff shape as orders-namespace.tf: the
# account-bearing key must not live in git, so it is materialized here as a k8s
# Secret rather than committed.
#
# The namespace is created here (not left to ArgoCD's CreateNamespace) so this
# Secret has somewhere to land before the in-cluster Prometheus Application (wave -2)
# and the Alloy DaemonSet sync into it. CreateNamespace=true on those Applications is
# then a harmless no-op.

resource "kubernetes_namespace" "observability" {
  metadata {
    name = "observability"
  }
}

# Temporal Cloud OpenMetrics bearer token (Metrics Read-Only SA). Key `api-key`
# matches the Prometheus Application's extraSecretMounts -> credentials_file path.
#
# Source precedence: the in-band token minted by the cloud layer's metricsread SA
# (remote state — the default path now that the provider is >= 1.x), else the
# out-of-band var.cloud_metrics_apikey (tcld), else empty. On the CLOUD backend it is
# created even when empty (keeps Prometheus bootable — its extraSecretMount resolves —
# so the SDK scrape, recording rule, and remote_write work; only the Cloud scrape job
# 401s until a real key is supplied). SKIPPED on the OSS backend: there is no Cloud
# metrics endpoint and applications.tf injects no extraSecretMount, so nothing mounts it.
resource "kubernetes_secret" "cloud_metrics_apikey" {
  count = local.is_oss ? 0 : 1
  metadata {
    name      = "cloud-metrics-apikey"
    namespace = kubernetes_namespace.observability.metadata[0].name
  }
  type = "Opaque"
  data = {
    "api-key" = try(coalesce(local.metrics_api_key, var.cloud_metrics_apikey), "")
  }
}
