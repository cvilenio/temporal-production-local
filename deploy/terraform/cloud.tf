# Temporal Cloud control plane (cloud profile only; gated on var.provision_cloud).
# When enabled, this creates the namespace + an API key the workers use; surface
# the key as an output / write it to a git-ignored file, never commit it.
#
# SKELETON: resource shapes follow the temporalio/temporalcloud provider; confirm
# exact argument names against the provider version pinned in versions.tf.

provider "temporalcloud" {
  api_key = var.temporal_cloud_api_key
}

# resource "temporalcloud_namespace" "ziggymart" {
#   count          = var.provision_cloud ? 1 : 0
#   name           = var.temporal_cloud_namespace
#   regions        = ["aws-us-east-1"]
#   retention_days = 30
#   # TODO: api_key_auth = true  (or certificate-based auth block)
# }

# resource "temporalcloud_service_account" "workers" {
#   count = var.provision_cloud ? 1 : 0
#   name  = "orders-workers"
#   # TODO: namespace access role binding to the namespace above
# }

# Output the endpoint so config/cloud.env can be populated.
# output "temporal_cloud_endpoint" {
#   value = var.provision_cloud ? "${temporalcloud_namespace.ziggymart[0].name}.tmprl.cloud:7233" : null
# }
