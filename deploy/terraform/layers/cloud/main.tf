# One namespace stack per entry, DRY via for_each over the namespace map. Every
# namespace (all business domains × envs) is managed from this single layer/state —
# review `terraform plan` carefully, a change here can touch every namespace at once.
module "namespaces" {
  source   = "../../modules/cloud-namespace"
  for_each = var.namespaces

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
}
