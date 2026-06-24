# Local backend in the hardened .secrets/ dir (chmod 700, git-ignored), matching
# the cloud layer. State here references the worker API key (pulled from the cloud
# layer's state into the k8s Secret), so treat the file as a secret.
#
# Path is relative to this layer dir: cluster -> layers -> terraform -> deploy -> repo root.
terraform {
  backend "local" {
    path = "../../../../.secrets/terraform/cluster.tfstate"
  }
}
