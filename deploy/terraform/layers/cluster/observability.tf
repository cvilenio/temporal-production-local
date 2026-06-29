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

# Temporal Cloud OpenMetrics bearer token (Metrics Read-Only SA, minted out-of-band
# via tcld — see deploy/argocd/applications/prometheus.yaml). Key `api-key` matches
# the Prometheus Application's extraSecretMounts -> credentials_file path. Created
# unconditionally even when the key is empty: that keeps Prometheus bootable (its
# extraSecretMount resolves) so the SDK scrape, recording rule, and remote_write all
# work; only the Cloud scrape job 401s until a real key is supplied.
resource "kubernetes_secret" "cloud_metrics_apikey" {
  metadata {
    name      = "cloud-metrics-apikey"
    namespace = kubernetes_namespace.observability.metadata[0].name
  }
  type = "Opaque"
  data = {
    "api-key" = var.cloud_metrics_apikey
  }
}
