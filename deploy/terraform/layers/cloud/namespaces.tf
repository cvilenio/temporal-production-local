locals {
  # Shared, backend-agnostic spec (config/temporal/namespaces.yaml) is the single
  # source of truth for namespace identity, search attributes, and retention —
  # the same file the local-OSS bootstrap reads, so Cloud and OSS cannot drift.
  spec = yamldecode(file("${path.module}/../../../../config/temporal/namespaces.yaml"))

  # Map each DOMAIN to a `<domain>` Cloud namespace (no environment axis — ADR-0017),
  # merging the shared spec (retention_days, search_attributes) with the Cloud-only
  # overlay (service account, API key, regions). The overlay var is a typed object
  # with optional() defaults, so omitted fields (regions, etc.) still resolve here.
  cloud_namespaces = {
    for domain, dcfg in local.spec.domains : domain => merge(
      {
        retention_days    = dcfg.retention_days
        search_attributes = dcfg.search_attributes
      },
      var.cloud_overlay[domain],
    )
  }
}

# One namespace stack per domain, DRY via for_each over the derived map. Every
# namespace (all business domains) is managed from this single layer/state — review
# `terraform plan` carefully, a change here can touch every namespace at once.
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
