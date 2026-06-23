# Local backend, with state kept in the hardened .secrets/ directory (chmod 700,
# git-ignored). Intentional: the demo uses no cloud resources outside Temporal Cloud,
# so there is no S3/GCS bucket — and co-locating state with the other secret-bearing
# artifacts (bootstrap key, connection envs) under .secrets/ keeps one hardened home.
#
# Path is relative to this layer dir: cloud -> layers -> terraform -> deploy -> repo root.
#
# State contains the worker API key secret in plaintext — treat the file as a secret.
# If it is lost, namespaces and service accounts are recoverable via `terraform import`
# (Temporal Cloud is the source of truth); the API key secret is not — rotate it. See
# README.md.
terraform {
  backend "local" {
    path = "../../../../.secrets/terraform/cloud.tfstate"
  }
}
#
# Real production would use an encrypted remote backend, e.g.:
#
# terraform {
#   backend "s3" {
#     bucket       = "my-tfstate"
#     key          = "temporal-cloud/cloud.tfstate"
#     region       = "us-east-1"
#     encrypt      = true
#     use_lockfile = true
#   }
# }
#
# A no-cloud alternative for tracked-but-safe state is SOPS + age (local age key);
# see README.md. Not enabled by default.
