locals {
  # Shared, backend-agnostic spec (config/temporal/namespaces.yaml) is the single
  # source of truth for namespace identity, search attributes, and retention —
  # the same file the local-OSS bootstrap reads, so Cloud and OSS cannot drift.
  spec = yamldecode(file("${path.module}/../../../../config/temporal/namespaces.yaml"))

  # Flatten domains × environments into the `<domain>-<env>` Cloud namespace map,
  # merging the shared spec (retention_days, search_attributes) with the
  # Cloud-only overlay (service account, API key, regions). The overlay var is a
  # typed object with optional() defaults, so omitted fields (regions, etc.)
  # still resolve here. Resulting keys/values must match the prior hand-written
  # map exactly — `terraform plan` is the no-churn gate (namespaces are
  # prevent_destroy).
  cloud_namespaces = {
    for pair in flatten([
      for domain, dcfg in local.spec.domains : [
        for env, ecfg in dcfg.environments : {
          key = "${domain}-${env}"
          value = {
            retention_days    = ecfg.retention_days
            search_attributes = dcfg.search_attributes
          }
        }
      ]
    ]) : pair.key => merge(pair.value, var.cloud_overlay[pair.key])
  }
}

# One namespace stack per entry, DRY via for_each over the derived map. Every
# namespace (all business domains × envs) is managed from this single layer/state —
# review `terraform plan` carefully, a change here can touch every namespace at once.
module "namespaces" {
  source   = "../../modules/cloud-namespace"
  for_each = local.cloud_namespaces

  account_id           = var.account_id
  namespace_name       = each.key
  regions              = each.value.regions
  retention_days       = each.value.retention_days
  service_account_name = each.value.service_account_name
  namespace_permission = each.value.namespace_permission
  account_access       = each.value.account_access
  create_api_key       = each.value.create_api_key
  api_key_display_name = each.value.api_key_display_name
  api_key_expiry_time  = each.value.api_key_expiry_time
  search_attributes    = each.value.search_attributes

  # Optional dedicated client SA (e.g. orders-api). null name => not minted.
  client_service_account_name = each.value.client_service_account_name
  client_api_key_display_name = each.value.client_api_key_display_name
  create_client_api_key       = each.value.create_client_api_key
  client_namespace_permission = each.value.client_namespace_permission
  client_account_access       = each.value.client_account_access
}
