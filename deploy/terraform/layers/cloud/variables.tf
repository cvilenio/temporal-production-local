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

# Account-level read-only observer identity for the platform-console (see
# observer.tf). Account-scoped read; powers the console's Cloud Ops API inventory
# (regions + namespaces) and per-namespace liveness probe. Off by default-safe:
# set create_observer = false to skip minting it.
variable "create_observer" {
  description = "Mint the read-only account-level observer service account for the platform-console's Cloud liveness + inventory probe."
  type        = bool
  default     = true
}

variable "observer_service_account_name" {
  description = "Name of the read-only observer service account."
  type        = string
  default     = "console-observer"
}

variable "create_observer_api_key" {
  description = "Mint the observer API key in Terraform (secret enters state). Set false to mint out-of-band via tcld."
  type        = bool
  default     = true
}

variable "observer_api_key_expiry_time" {
  description = "RFC3339 expiry for the observer API key (e.g. 2027-06-23T00:00:00Z)."
  type        = string
  default     = "2027-06-23T00:00:00Z"
}

# Account-level Metrics Read-Only identity for the in-cluster Prometheus scrape of
# the Cloud OpenMetrics endpoint (see metrics-reader.tf). metricsread role; requires
# provider >= 1.x. Default-on like the observer; set create_metrics_reader = false
# to skip minting it.
variable "create_metrics_reader" {
  description = "Mint the account-level Metrics Read-Only service account for the in-cluster Prometheus OpenMetrics scrape (ADR-0021)."
  type        = bool
  default     = true
}

variable "metrics_reader_service_account_name" {
  description = "Name of the Metrics Read-Only service account."
  type        = string
  default     = "metrics-reader"
}

variable "create_metrics_reader_api_key" {
  description = "Mint the metrics-reader API key in Terraform (secret enters state, consumed in-band by the cluster layer). Set false to mint out-of-band via tcld."
  type        = bool
  default     = true
}

variable "metrics_reader_api_key_expiry_time" {
  description = "RFC3339 expiry for the metrics-reader API key (e.g. 2027-06-23T00:00:00Z)."
  type        = string
  default     = "2027-06-23T00:00:00Z"
}

# Cloud-only overlay, keyed by DOMAIN (`<domain>`, e.g. ziggymart) — no environment
# axis (ADR-0017). The SHARED config (retention, search attributes) lives in
# config/temporal/namespaces.yaml and is merged in via locals (see namespaces.tf) —
# do NOT duplicate it here. This overlay carries only the fields with no OSS analog:
# the worker service account, the API key, and region placement.
#
# One entry per domain in the spec. A new business domain = add it to the spec AND
# add its overlay entry here (its own SA/key → enables Nexus + per-domain auth);
# `regions` is the per-domain multi-region axis.
variable "cloud_overlay" {
  description = "Per-namespace Cloud-only settings (service account, API key, regions), keyed by `<domain>`. Shared config comes from the spec, not here."
  type = map(object({
    service_account_name = string
    api_key_display_name = string
    api_key_expiry_time  = string
    create_api_key       = optional(bool, true)
    namespace_permission = optional(string, "write")
    account_access       = optional(string, "read")
    regions              = optional(list(string), ["aws-us-east-1"])
    # Optional dedicated CLIENT service account (e.g. orders-api). Omit to skip.
    client_service_account_name = optional(string)
    client_api_key_display_name = optional(string)
    create_client_api_key       = optional(bool, true)
    client_namespace_permission = optional(string, "write")
    client_account_access       = optional(string, "read")
  }))
}
