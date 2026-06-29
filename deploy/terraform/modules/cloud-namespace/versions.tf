# Reusable building block: one Temporal Cloud namespace + a service account + an
# (optional) API key scoped to it. Child module — declares only the provider it needs,
# never a provider block (the calling layer configures the provider). This keeps the
# module composable across layers.

terraform {
  required_version = ">= 1.6"

  required_providers {
    temporalcloud = {
      source = "temporalio/temporalcloud"
      # Kept in lockstep with the calling layer's pin (cloud/versions.tf) — bumped to
      # 1.x for the `metricsread` account role. v1 is additive on the namespace /
      # service_account / apikey resources this module uses.
      version = "~> 1.5"
    }
  }
}
