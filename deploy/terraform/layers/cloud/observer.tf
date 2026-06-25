# Read-only, account-level OBSERVER identity for the platform-console.
#
# Distinct from the per-namespace worker/client identities (namespaces.tf): this
# one is account-scoped `read` and carries NO namespace_accesses. The console uses
# its API key against the Temporal Cloud Ops API (saas-api.tmprl.cloud) to call
# GetNamespaces / GetRegions and render the multi-region / multi-namespace status
# block on the architecture page, plus DescribeNamespace for per-namespace
# liveness. Least privilege on purpose — the console only observes, so the
# credential can only read (mirrors the read-only `console-reader` ServiceAccount
# on the kube side; see ADR-0015). It can never mutate a namespace or workflow.
resource "temporalcloud_service_account" "observer" {
  count          = var.create_observer ? 1 : 0
  name           = var.observer_service_account_name
  account_access = "read"

  # Account-level `read` grants the Cloud Ops API call surface (GetNamespaces /
  # GetRegions), but a read-only principal only SEES namespaces it is explicitly
  # assigned — so grant read on every managed namespace. Still least-privilege:
  # read-only, no write/admin anywhere. Without these, GetNamespaces returns empty.
  namespace_accesses = [
    for env, m in module.namespaces : {
      namespace_id = m.namespace_id
      permission   = "read"
    }
  ]
}

# Observer API key. Optional: when create_observer_api_key is false the key is
# minted out-of-band (tcld) so its secret never enters Terraform state.
resource "temporalcloud_apikey" "observer" {
  count = var.create_observer && var.create_observer_api_key ? 1 : 0

  display_name = "${var.observer_service_account_name}-key"
  owner_type   = "service-account"
  owner_id     = temporalcloud_service_account.observer[0].id
  expiry_time  = var.observer_api_key_expiry_time
  disabled     = false
}
