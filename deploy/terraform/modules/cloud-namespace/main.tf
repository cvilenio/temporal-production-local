# Namespace: API-key auth only (no accepted_client_ca → cert auth is rejected).
resource "temporalcloud_namespace" "this" {
  name           = var.namespace_name
  regions        = var.regions
  retention_days = var.retention_days
  api_key_auth   = true

  # Guard every namespace against accidental destroy. The Cloud layer uses a single
  # state for both envs, so a stray `terraform destroy` would otherwise hit prod too.
  # To intentionally tear an env down, target it explicitly or remove this guard.
  lifecycle {
    prevent_destroy = true
  }
}

# Service account the workers authenticate as. account_access is the baseline
# account-level role (least privilege = read); the actual namespace permission is
# granted explicitly via namespace_accesses. Note: an account_access of "admin" cannot
# carry explicit namespace_accesses (admins get all namespaces implicitly).
resource "temporalcloud_service_account" "workers" {
  name           = var.service_account_name
  account_access = var.account_access

  namespace_accesses = [
    {
      namespace_id = temporalcloud_namespace.this.id
      permission   = var.namespace_permission
    }
  ]
}

# Custom search attributes — namespace setup, declared here (the OSS equivalent is the
# temporal-search-attributes bootstrap container in compose/oss-server.yml). On Cloud
# these are control-plane operations; the provider handles them without a data-plane key.
resource "temporalcloud_namespace_search_attribute" "this" {
  for_each = var.search_attributes

  namespace_id = temporalcloud_namespace.this.id
  name         = each.key
  type         = each.value
}

# Worker API key. Optional: when create_api_key is false the key is minted out-of-band
# (tcld) so its secret never enters Terraform state.
resource "temporalcloud_apikey" "workers" {
  count = var.create_api_key ? 1 : 0

  display_name = var.api_key_display_name
  owner_type   = "service-account"
  owner_id     = temporalcloud_service_account.workers.id
  expiry_time  = var.api_key_expiry_time
  disabled     = false
}
