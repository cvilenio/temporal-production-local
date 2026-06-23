output "namespace_id" {
  description = "Provider resource id for the namespace (used for service-account binding and terraform import)."
  value       = temporalcloud_namespace.this.id
}

# TEMPORAL_NAMESPACE for clients is the <name>.<account> handle. Derived explicitly from
# name + account_id rather than from .id, in case the provider returns a UUID for .id.
output "namespace_handle" {
  description = "Client-facing namespace handle, e.g. ziggymart-nonprod.<account-id> (set TEMPORAL_NAMESPACE to this)."
  value       = "${temporalcloud_namespace.this.name}.${var.account_id}"
}

output "endpoint" {
  description = "gRPC endpoint for TEMPORAL_ADDRESS (from the provider's computed API-key endpoint; falls back to the derived form)."
  value       = coalesce(temporalcloud_namespace.this.endpoints.grpc_address, "${temporalcloud_namespace.this.name}.${var.account_id}.tmprl.cloud:7233")
}

output "web_address" {
  description = "Temporal Cloud Web UI address for the namespace."
  value       = temporalcloud_namespace.this.endpoints.web_address
}

output "service_account_id" {
  description = "Service account id (use with `tcld apikey create` when create_api_key is false)."
  value       = temporalcloud_service_account.workers.id
}

output "api_key_token" {
  description = "Worker API key secret (null when create_api_key is false). SENSITIVE — present in state."
  value       = try(temporalcloud_apikey.workers[0].token, null)
  sensitive   = true
}
