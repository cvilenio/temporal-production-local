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
