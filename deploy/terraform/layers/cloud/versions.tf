# Cloud layer — the base control-plane layer. Provisions Temporal Cloud namespaces,
# service accounts, and worker API keys for ALL environments on account <account-id>.
#
# Deliberately depends on ONLY the temporalcloud provider: `terraform init` here must
# not pull kind/kubernetes/helm. That independence is the point of the layering — the
# Cloud layer applies with no cluster present.

terraform {
  required_version = ">= 1.6"

  required_providers {
    temporalcloud = {
      source = "temporalio/temporalcloud"
      # 1.x: the `metricsread` account role landed after v1.0 (a Metrics Read-Only
      # service account for the OpenMetrics scrape — see metrics-reader.tf). 0.9's
      # account_access only allowed admin/developer/read; the cloud-namespace module
      # already validated `metricsread` in anticipation, blocked only by this pin.
      # v1 is additive on the resources this layer already uses (namespace,
      # service_account, apikey, search_attribute) — review `terraform plan` before
      # applying to the live account.
      version = "~> 1.5"
    }
  }
}
