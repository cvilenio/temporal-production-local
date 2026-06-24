# The orders workload's k8s namespace and the Cloud worker credential seeded into
# it. This is the credential handoff from the cloud layer: the account-bearing API
# key must not live in git (see .githooks/pre-commit), so it is materialized here
# from cloud remote state (see remote-state.tf) rather than committed. Everything
# that RUNS in this namespace is reconciled by ArgoCD (ADR-0002).

resource "kubernetes_namespace" "orders" {
  metadata {
    name = var.orders_namespace
  }
}

# Worker API key as an Opaque Secret. Key `api-key` matches the chart's
# apiKeySecretRef.key; the Worker Controller injects it as TEMPORAL_API_KEY.
resource "kubernetes_secret" "orders_cloud_apikey" {
  metadata {
    name      = var.cloud_apikey_secret_name
    namespace = kubernetes_namespace.orders.metadata[0].name
  }
  type = "Opaque"
  data = {
    "api-key" = local.worker_api_key
  }
}
