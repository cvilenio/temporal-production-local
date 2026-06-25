# Read the cloud layer's outputs. This is the layer boundary: the cloud layer
# owns the Temporal Cloud resources and exposes the worker credential + endpoint;
# this layer materializes them into the cluster. Keeping it a data source (not a
# duplicated tfvars) means there is one source of truth for the API key.
data "terraform_remote_state" "cloud" {
  backend = "local"
  config = {
    path = var.cloud_state_path
  }
}

locals {
  # Per-domain outputs are keyed by the bare `<domain>` name (no env axis).
  worker_api_key   = data.terraform_remote_state.cloud.outputs.api_key_tokens[var.cloud_namespace]
  namespace_handle = data.terraform_remote_state.cloud.outputs.namespace_handles[var.cloud_namespace]
  # Dedicated client key for orders-api (null if the cloud layer didn't mint one).
  client_api_key = try(data.terraform_remote_state.cloud.outputs.client_api_key_tokens[var.cloud_namespace], null)
  # For api_key_auth namespaces the cloud `endpoint` output is already the regional
  # gRPC endpoint (e.g. us-east-1.aws.api.temporal.io:7233) that API keys require.
  temporal_address = data.terraform_remote_state.cloud.outputs.endpoints[var.cloud_namespace]
}
