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

# Dedicated CLIENT service account + key (e.g. orders-api: starts/signals
# workflows). Separate identity from the workers (ADR-0008) so the blast radius of
# the client credential is independent of the worker fleet's. Minted only when a
# client SA name is supplied for this namespace.
resource "temporalcloud_service_account" "client" {
  count          = var.client_service_account_name != null ? 1 : 0
  name           = var.client_service_account_name
  account_access = var.client_account_access

  namespace_accesses = [
    {
      namespace_id = temporalcloud_namespace.this.id
      permission   = var.client_namespace_permission
    }
  ]
}

resource "temporalcloud_apikey" "client" {
  count = var.client_service_account_name != null && var.create_client_api_key ? 1 : 0

  display_name = coalesce(var.client_api_key_display_name, "${var.client_service_account_name}-key")
  owner_type   = "service-account"
  owner_id     = temporalcloud_service_account.client[0].id
  expiry_time  = var.api_key_expiry_time
  disabled     = false
}
