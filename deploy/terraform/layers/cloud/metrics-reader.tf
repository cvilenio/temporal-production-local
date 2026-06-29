# Account-level Metrics Read-Only identity for the in-cluster Prometheus scrape of
# the Temporal Cloud OpenMetrics endpoint (metrics.temporal.io — ADR-0021).
#
# Distinct from the worker/client identities (namespaces.tf) and the observer
# (observer.tf): this one carries the `metricsread` account role and NOTHING else —
# no namespace_accesses, no Ops API. metricsread is account-scoped by design (the
# OpenMetrics endpoint serves the whole account), so it needs no per-namespace grant.
# Least privilege: it can only read metrics, never a workflow, namespace, or the Ops
# API. Requires provider >= 1.x (see versions.tf); 0.9 could not express this role.
resource "temporalcloud_service_account" "metrics_reader" {
  count          = var.create_metrics_reader ? 1 : 0
  name           = var.metrics_reader_service_account_name
  account_access = "metricsread"
}

# Metrics-reader API key. Optional: when create_metrics_reader_api_key is false the
# key is minted out-of-band (tcld --account-role MetricsRead) so its secret never
# enters Terraform state — same escape hatch as the observer key. When true, the
# token is exported (sensitive) and the cluster layer materializes it into the
# cloud-metrics-apikey Secret, fully in-band.
resource "temporalcloud_apikey" "metrics_reader" {
  count = var.create_metrics_reader && var.create_metrics_reader_api_key ? 1 : 0

  display_name = "${var.metrics_reader_service_account_name}-key"
  owner_type   = "service-account"
  owner_id     = temporalcloud_service_account.metrics_reader[0].id
  expiry_time  = var.metrics_reader_api_key_expiry_time
  disabled     = false
}
