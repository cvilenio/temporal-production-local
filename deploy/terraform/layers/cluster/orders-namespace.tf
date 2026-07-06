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
# CLOUD ONLY — on the OSS backend the worker credential is a cert-manager mTLS
# client cert issued by the temporal-server chart into this namespace, not an API key.
resource "kubernetes_secret" "orders_cloud_apikey" {
  count = local.is_oss ? 0 : 1
  metadata {
    name      = var.cloud_apikey_secret_name
    namespace = kubernetes_namespace.orders.metadata[0].name
  }
  type = "Opaque"
  data = {
    "api-key" = local.worker_api_key
  }
}

# Dedicated CLIENT API key for orders-api (starts/signals workflows). Separate
# identity + Secret from the workers (ADR-0008). Seeded only when the cloud layer
# minted a client key for this namespace. CLOUD ONLY (see above). orders-app reads
# it via the chart's connection.apiKeySecret -> TEMPORAL_API_KEY.
resource "kubernetes_secret" "orders_client_apikey" {
  count = (local.is_oss || local.client_api_key == null) ? 0 : 1
  metadata {
    name      = var.client_apikey_secret_name
    namespace = kubernetes_namespace.orders.metadata[0].name
  }
  type = "Opaque"
  data = {
    "api-key" = local.client_api_key
  }
}
