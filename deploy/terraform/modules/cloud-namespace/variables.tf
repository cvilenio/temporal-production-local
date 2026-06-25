variable "namespace_name" {
  description = "Temporal Cloud namespace name, e.g. ziggymart (lowercase letters, numbers, hyphens; 2-64 chars)."
  type        = string
}

variable "regions" {
  description = "Cloud regions for the namespace (1-2). AWS us-east-1 is aws-us-east-1."
  type        = list(string)
  default     = ["aws-us-east-1"]
}

variable "retention_days" {
  description = "Event history retention in days for this namespace."
  type        = number
}

variable "service_account_name" {
  description = "Name of the service account that owns the worker API key."
  type        = string
}

variable "namespace_permission" {
  description = "Permission the service account holds on this namespace: admin | write | read. Workers need write."
  type        = string
  default     = "write"

  validation {
    condition     = contains(["admin", "write", "read"], var.namespace_permission)
    error_message = "namespace_permission must be one of: admin, write, read."
  }
}

variable "account_access" {
  description = "Baseline account-level role for the service account: admin | developer | read | metricsread. Least privilege is read; namespace access is granted separately. (admin cannot carry explicit namespace_accesses.)"
  type        = string
  default     = "read"

  validation {
    condition     = contains(["admin", "developer", "read", "metricsread"], var.account_access)
    error_message = "account_access must be one of: admin, developer, read, metricsread."
  }
}

variable "create_api_key" {
  description = "If true, Terraform mints the worker API key (secret lands in state). If false, mint it out-of-band with tcld against the service account id output."
  type        = bool
  default     = true
}

variable "api_key_display_name" {
  description = "Display name for the worker API key."
  type        = string
}

variable "api_key_expiry_time" {
  description = "RFC3339 expiry for the API key (required by the provider; no infinite key). Ignored when create_api_key is false."
  type        = string
}

variable "account_id" {
  description = "Temporal Cloud account id (the short account suffix, e.g. <account-id>). Used to derive the gRPC endpoint."
  type        = string
}

# --- Optional dedicated CLIENT service account (e.g. orders-api) -------------
# A second identity, separate from the worker SA, for the process that STARTS and
# signals workflows (ADR-0008: client vs worker identity separation). Minted only
# when client_service_account_name is non-null, so namespaces that don't run an
# in-cluster client (or run the client on the worker identity) are unaffected.
variable "client_service_account_name" {
  description = "Name of the dedicated CLIENT service account (starts/signals workflows). null = do not mint a client SA for this namespace."
  type        = string
  default     = null
}

variable "client_namespace_permission" {
  description = "Permission the client SA holds on this namespace: admin | write | read. A workflow starter needs write."
  type        = string
  default     = "write"

  validation {
    condition     = contains(["admin", "write", "read"], var.client_namespace_permission)
    error_message = "client_namespace_permission must be one of: admin, write, read."
  }
}

variable "client_account_access" {
  description = "Baseline account-level role for the client SA: admin | developer | read | metricsread. Least privilege is read."
  type        = string
  default     = "read"

  validation {
    condition     = contains(["admin", "developer", "read", "metricsread"], var.client_account_access)
    error_message = "client_account_access must be one of: admin, developer, read, metricsread."
  }
}

variable "client_api_key_display_name" {
  description = "Display name for the client API key. Defaults to \"<client_service_account_name>-key\" when null."
  type        = string
  default     = null
}

variable "create_client_api_key" {
  description = "If true (and a client SA is minted), Terraform mints the client API key (secret lands in state). The key reuses api_key_expiry_time."
  type        = bool
  default     = true
}

variable "search_attributes" {
  description = "Custom search attributes for this namespace, as name => type. Valid types: Text, Keyword, Int, Double, Datetime, Bool, KeywordList."
  type        = map(string)
  default     = {}
}
