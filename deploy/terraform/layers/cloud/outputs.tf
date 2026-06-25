# Outputs are keyed by environment. Consumers (compose cloud profile now, the cluster
# layer's k8s Secret later) read these via `terraform output` — the Cloud layer itself
# writes nothing to compose or Kubernetes, preserving layer independence.

output "endpoints" {
  description = "Per-env gRPC endpoints (TEMPORAL_ADDRESS)."
  value       = { for env, m in module.namespaces : env => m.endpoint }
}

output "namespace_handles" {
  description = "Per-env client namespace handles (TEMPORAL_NAMESPACE), e.g. ziggymart-nonprod.<account-id>."
  value       = { for env, m in module.namespaces : env => m.namespace_handle }
}

output "namespace_ids" {
  description = "Per-env provider resource ids (for terraform import / reference)."
  value       = { for env, m in module.namespaces : env => m.namespace_id }
}

output "service_account_ids" {
  description = "Per-env service account ids (use with `tcld apikey create` when minting keys out-of-band)."
  value       = { for env, m in module.namespaces : env => m.service_account_id }
}

output "api_key_tokens" {
  description = "Per-env worker API key secrets (null where create_api_key is false). SENSITIVE."
  value       = { for env, m in module.namespaces : env => m.api_key_token }
  sensitive   = true
}

output "client_service_account_ids" {
  description = "Per-env CLIENT service account ids (null where no client SA is minted)."
  value       = { for env, m in module.namespaces : env => m.client_service_account_id }
}

output "client_api_key_tokens" {
  description = "Per-env CLIENT API key secrets (null where no client SA/key is minted). SENSITIVE — consumed by the cluster layer's orders-client-apikey Secret."
  value       = { for env, m in module.namespaces : env => m.client_api_key_token }
  sensitive   = true
}
