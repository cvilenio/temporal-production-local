variable "temporal_cloud_api_key" {
  description = "Bootstrap (account-level, namespace-admin) Temporal Cloud API key for the provider. Supply via env, never commit. The provider also reads TEMPORAL_CLOUD_API_KEY directly."
  type        = string
  sensitive   = true
  default     = ""
}

variable "account_id" {
  description = "Temporal Cloud account id (short suffix). Pins the provider and derives endpoints. No committed default — supply via TF_VAR_account_id (source .secrets/account.env) to keep the account id out of git."
  type        = string
}

# Cloud-only overlay, keyed by the FULL namespace name (`<domain>-<env>`, e.g.
# ziggymart-nonprod). The SHARED config (retention, search attributes) lives in
# config/temporal/namespaces.yaml and is merged in via locals (see main.tf) — do
# NOT duplicate it here. This overlay carries only the fields with no OSS analog:
# the worker service account, the API key, and region placement.
#
# There must be one entry per `<domain>-<env>` produced by the spec. Add a new
# business domain by adding it to the spec AND adding its overlay entries here.
variable "cloud_overlay" {
  description = "Per-namespace Cloud-only settings (service account, API key, regions), keyed by `<domain>-<env>`. Shared config comes from the spec, not here."
  type = map(object({
    service_account_name = string
    api_key_display_name = string
    api_key_expiry_time  = string
    create_api_key       = optional(bool, true)
    namespace_permission = optional(string, "write")
    account_access       = optional(string, "read")
    regions              = optional(list(string), ["aws-us-east-1"])
  }))
}
