# Outputs are keyed by DOMAIN (`<domain>`, no environment axis). Consumers (the host
# cloud profile, the cluster layer's k8s Secret) read these via `terraform output` —
# the Cloud layer itself writes nothing to compose or Kubernetes, preserving layer
# independence.

output "endpoints" {
  description = "Per-domain gRPC endpoints (TEMPORAL_ADDRESS)."
  value       = { for domain, m in module.namespaces : domain => m.endpoint }
}

output "namespace_handles" {
  description = "Per-domain client namespace handles (TEMPORAL_NAMESPACE), e.g. ziggymart.<account-id>."
  value       = { for domain, m in module.namespaces : domain => m.namespace_handle }
}

output "namespace_ids" {
  description = "Per-domain provider resource ids (for terraform import / reference)."
  value       = { for domain, m in module.namespaces : domain => m.namespace_id }
}

output "service_account_ids" {
  description = "Per-domain service account ids (use with `tcld apikey create` when minting keys out-of-band)."
  value       = { for domain, m in module.namespaces : domain => m.service_account_id }
}

output "api_key_tokens" {
  description = "Per-domain worker API key secrets (null where create_api_key is false). SENSITIVE."
  value       = { for domain, m in module.namespaces : domain => m.api_key_token }
  sensitive   = true
}

output "client_service_account_ids" {
  description = "Per-domain CLIENT service account ids (null where no client SA is minted)."
  value       = { for domain, m in module.namespaces : domain => m.client_service_account_id }
}

output "client_api_key_tokens" {
  description = "Per-domain CLIENT API key secrets (null where no client SA/key is minted). SENSITIVE — consumed by the cluster layer's orders-client-apikey Secret."
  value       = { for domain, m in module.namespaces : domain => m.client_api_key_token }
  sensitive   = true
}

output "observer_service_account_id" {
  description = "Read-only account-level observer service account id (null when create_observer is false)."
  value       = var.create_observer ? temporalcloud_service_account.observer[0].id : null
}

output "observer_api_key_token" {
  description = "Read-only observer API key secret for the platform-console's Cloud Ops/liveness probe (null when not minted in Terraform). SENSITIVE."
  value       = var.create_observer && var.create_observer_api_key ? temporalcloud_apikey.observer[0].token : null
  sensitive   = true
}
