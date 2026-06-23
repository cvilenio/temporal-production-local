# Reusable building block: one Temporal Cloud namespace + a service account + an
# (optional) API key scoped to it. Child module — declares only the provider it needs,
# never a provider block (the calling layer configures the provider). This keeps the
# module composable across layers.

terraform {
  required_version = ">= 1.6"

  required_providers {
    temporalcloud = {
      source  = "temporalio/temporalcloud"
      version = "~> 0.9"
    }
  }
}
