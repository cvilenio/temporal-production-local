# Read the cloud layer's outputs. This is the layer boundary: the cloud layer
# owns the Temporal Cloud resources and exposes the worker credential + endpoint;
# this layer materializes them into the cluster. Keeping it a data source (not a
# duplicated tfvars) means there is one source of truth for the API key.
#
# On the OSS backend the cloud state is NOT read (count = 0): an OSS-only run must
# not require .secrets/terraform/cloud.tfstate to exist. Every connection value is
# then derived from the OSS vars instead. The connection *shape* is identical on
# both backends (TLS on, a mounted credential) — only the credential TYPE differs
# (Cloud API key ↔ OSS client cert), so the swap stays a config change (ADR-0005).

locals {
  is_oss = var.temporal_backend == "oss"
}

data "terraform_remote_state" "cloud" {
  count   = local.is_oss ? 0 : 1
  backend = "local"
  config = {
    path = var.cloud_state_path
  }
}

locals {
  cloud_out = local.is_oss ? null : data.terraform_remote_state.cloud[0].outputs

  orders_descriptor = yamldecode(
    file("${path.module}/../../../../config/domains/orders.yaml")
  )
  orders_temporal_namespace = coalesce(
    try(local.orders_descriptor.namespace, null),
    local.orders_descriptor.domain,
  )
  oss_namespace_effective = var.oss_namespace != "" ? var.oss_namespace : local.orders_temporal_namespace

  # Per-domain outputs are keyed by the bare `<domain>` name (no env axis).
  worker_api_key   = local.is_oss ? null : local.cloud_out.api_key_tokens[var.cloud_namespace]
  namespace_handle = local.is_oss ? local.oss_namespace_effective : local.cloud_out.namespace_handles[var.cloud_namespace]
  # Dedicated client key for orders-api (null if the cloud layer didn't mint one).
  client_api_key = local.is_oss ? null : try(local.cloud_out.client_api_key_tokens[var.cloud_namespace], null)
  # Metrics Read-Only key for the in-cluster Prometheus OpenMetrics scrape (ADR-0021).
  # null on OSS (no Cloud metrics endpoint) and when the cloud layer minted it
  # out-of-band; observability.tf falls back to var.cloud_metrics_apikey.
  metrics_api_key = local.is_oss ? null : try(local.cloud_out.metrics_reader_api_key_token, null)
  # For api_key_auth namespaces the cloud `endpoint` output is already the regional
  # gRPC endpoint (e.g. us-east-1.aws.api.temporal.io:7233) that API keys require.
  # On OSS this is the in-cluster frontend address.
  temporal_address = local.is_oss ? var.oss_temporal_address : local.cloud_out.endpoints[var.cloud_namespace]

  # TLS stays ON in both modes. The credential ref that the workers/apps charts
  # consume switches by backend: Cloud sets an apiKeySecret, OSS sets an mtlsSecret
  # (the cert-manager client cert the temporal-server chart issues into the orders ns).
  # Exactly one is non-empty per backend; "" tells the chart to omit that branch.
  worker_apikey_secret   = local.is_oss ? "" : var.cloud_apikey_secret_name
  client_apikey_secret   = local.is_oss ? "" : var.client_apikey_secret_name
  worker_mtls_secret     = local.is_oss ? var.oss_worker_mtls_secret : ""
  client_mtls_secret     = local.is_oss ? var.oss_client_mtls_secret : ""
  autoscaler_mtls_secret = local.is_oss ? var.oss_autoscaler_mtls_secret : ""
}
