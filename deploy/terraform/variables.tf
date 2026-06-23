variable "cluster_name" {
  type    = string
  default = "temporal-demo"
}

# Set true to also provision Temporal Cloud (namespace + API key) for the cloud
# profile. Left false for the default self-hosted-on-kind backend.
variable "provision_cloud" {
  type    = bool
  default = false
}

variable "temporal_cloud_namespace" {
  type    = string
  default = "ziggymart"
}

# Temporal Cloud API key for the provider itself (export TEMPORAL_CLOUD_API_KEY).
variable "temporal_cloud_api_key" {
  type      = string
  default   = ""
  sensitive = true
}
