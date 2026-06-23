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
      source  = "temporalio/temporalcloud"
      version = "~> 0.9"
    }
  }
}
