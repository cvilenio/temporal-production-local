terraform {
  required_version = ">= 1.6"

  required_providers {
    # Local kind cluster lifecycle.
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.9"
    }
    # Temporal Cloud (namespaces, users, API keys). Used only for the cloud profile.
    temporalcloud = {
      source  = "temporalio/temporalcloud"
      version = "~> 0.8"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}
