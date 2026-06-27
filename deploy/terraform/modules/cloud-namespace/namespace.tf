# Namespace: API-key auth only (no accepted_client_ca → cert auth is rejected).
resource "temporalcloud_namespace" "this" {
  name           = var.namespace_name
  regions        = var.regions
  retention_days = var.retention_days
  api_key_auth   = true

  # Guard every namespace against accidental destroy. The Cloud layer uses a single
  # state for ALL domains, so a stray `terraform destroy` would otherwise hit every
  # namespace at once. To intentionally tear a namespace down (e.g. a rename — Cloud
  # namespaces can't be renamed in place), set this to false, apply, then restore it.
  lifecycle {
    prevent_destroy = true
  }
}

# Custom search attributes — namespace setup, declared here (the OSS equivalent is the
# temporal-search-attributes bootstrap container in compose/oss-server.yml). On Cloud
# these are control-plane operations; the provider handles them without a data-plane key.
#
# The shared spec (config/temporal/namespaces.yaml) uses the CLI-style type names
# (Text, Keyword, KeywordList) the OSS bootstrap feeds to `temporal operator
# search-attribute create`. The Cloud provider's enum spells the multi-word type
# differently (keyword_list); translate just that one so the single-word types pass
# through unchanged (no churn on the already-provisioned attributes).
resource "temporalcloud_namespace_search_attribute" "this" {
  for_each = var.search_attributes

  namespace_id = temporalcloud_namespace.this.id
  name         = each.key
  type         = each.value == "KeywordList" ? "keyword_list" : each.value
}
