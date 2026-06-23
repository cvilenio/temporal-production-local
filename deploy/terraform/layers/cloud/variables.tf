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

# Keyed by the FULL namespace name (e.g. ziggymart-nonprod) so multiple business
# domains can coexist on the one account — add ziggymart-prod, payments-nonprod, etc.
# as new keys. The <domain>-<env> convention encodes the environment in the name.
variable "namespaces" {
  description = "Map of namespace name -> Temporal Cloud namespace config. One namespace + service account (+ optional API key) is provisioned per entry."
  type = map(object({
    retention_days       = number
    service_account_name = string
    api_key_display_name = string
    api_key_expiry_time  = string
    create_api_key       = optional(bool, true)
    namespace_permission = optional(string, "write")
    account_access       = optional(string, "read")
    regions              = optional(list(string), ["aws-us-east-1"])
    search_attributes    = optional(map(string), {})
  }))
}
