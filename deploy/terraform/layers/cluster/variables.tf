# Paths are relative to this layer dir: cluster -> layers -> terraform -> deploy -> repo root.

variable "kubeconfig_path" {
  description = "kubeconfig for the kind cluster (written by deploy/kind/cluster-up.sh)."
  type        = string
  default     = "../../../../.secrets/kube/kind.kubeconfig"
}

variable "kube_context" {
  description = "kubeconfig context for the kind cluster."
  type        = string
  default     = "kind-kind"
}

variable "cloud_state_path" {
  description = "Path to the cloud layer's local state (source of the worker API key, endpoint, and namespace handle)."
  type        = string
  default     = "../../../../.secrets/terraform/cloud.tfstate"
}

variable "cloud_env" {
  description = "Which cloud namespace this cluster mirrors — the key into the cloud layer's per-namespace outputs (<domain>-<env>)."
  type        = string
  default     = "ziggymart-nonprod"
}

variable "orders_namespace" {
  description = "Kubernetes namespace for the orders workload."
  type        = string
  default     = "orders"
}

variable "argocd_namespace" {
  description = "Kubernetes namespace for ArgoCD."
  type        = string
  default     = "argocd"
}

# argo-cd chart version + repo, nginx proxy image, and the add-on chart versions
# now come from config/dependencies.yaml (read via yamldecode in main.tf), so they
# are not Terraform variables — one source of truth.

variable "cloud_apikey_secret_name" {
  description = "Name of the k8s Secret holding the Cloud worker API key (consumed by the orders-workers chart's apiKeySecretRef)."
  type        = string
  default     = "orders-cloud-apikey"
}

# ArgoCD pulls ALL charts from the local OCI registry (deploy/kind/mirror-deps.sh
# for third-party, just chart-publish for orders-workers) — no GitHub/public-internet
# dependency for delivery. This is the in-cluster Service address of the registry.
variable "oci_charts_repo" {
  description = "In-cluster OCI Helm repo ArgoCD pulls charts from — the TLS proxy in front of the HTTP registry."
  type        = string
  default     = "registry-tls.kube-public.svc:5000/charts"
}

variable "registry_service" {
  description = "Name of the in-cluster Service for the HTTP registry (created by cluster-up.sh), proxied by registry-tls."
  type        = string
  default     = "artifact-registry"
}

variable "orders_workers_chart_version" {
  description = "Published version of the orders-workers OCI chart (matches Chart.yaml / just chart-publish)."
  type        = string
  default     = "0.1.0"
}

variable "worker_image_tag" {
  description = "Tag for the orders worker images (fallback when a digest is not pinned; see worker_image_digests)."
  type        = string
  default     = "latest"
}

variable "worker_image_digests" {
  description = "Per-profile image digests (sha256:...) from `just ci`. When set, workers are pinned by digest (immutable, content-addressed Build ID) instead of tag."
  type        = map(string)
  default     = {} # e.g. { workflow = "sha256:...", activity = "sha256:..." }
}
