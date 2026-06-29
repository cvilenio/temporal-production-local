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

variable "cloud_namespace" {
  description = "Which cloud namespace (domain) this cluster mirrors — the key into the cloud layer's per-domain outputs (`<domain>`, no env axis)."
  type        = string
  default     = "ziggymart"
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

variable "client_apikey_secret_name" {
  description = "Name of the k8s Secret holding the Cloud CLIENT API key (consumed by orders-api via the orders-app chart's connection.apiKeySecret)."
  type        = string
  default     = "orders-client-apikey"
}

# Temporal Cloud OpenMetrics API key for the in-cluster Prometheus scrape (ADR-0021).
# FALLBACK source only: the default path is the in-band metricsread SA the cloud layer
# mints (provider >= 1.x), consumed via remote state (see remote-state.tf /
# observability.tf). Set this (TF_VAR_cloud_metrics_apikey, never committed —
# .githooks/pre-commit) only when the key is minted out-of-band via tcld
# (create_metrics_reader_api_key = false). Empty is allowed: the Secret is still
# created (so Prometheus boots and the SDK scrape + remote_write work), the Cloud
# scrape job just 401s until a real key is supplied.
variable "cloud_metrics_apikey" {
  description = "Out-of-band fallback for the Cloud Metrics Read-Only API key (Bearer). Normally minted in-band by the cloud layer; set only when create_metrics_reader_api_key = false. Empty disables only the Cloud scrape."
  type        = string
  default     = ""
  sensitive   = true
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
  default     = "0.1.7"
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

variable "orders_data_chart_version" {
  description = "Published version of the orders-data OCI chart (CNPG orders-db + credential)."
  type        = string
  default     = "0.1.0"
}

variable "orders_api_chart_version" {
  description = "Published version of the orders-api OCI chart (orders-api Deployment + Service)."
  type        = string
  default     = "0.1.2"
}

variable "alloy_chart_version" {
  description = "Published version of the alloy OCI chart (Grafana Alloy log-collection DaemonSet; matches Chart.yaml / just chart-publish)."
  type        = string
  default     = "0.4.0"
}

# The committed log pipeline (ADR-0020): Alloy builds an OTel LogRecord from each
# pod stdout line and ships it OTLP to the host-side OTel Collector, which writes
# to ClickHouse. Single path — no Loki, no opt-in gate.
variable "alloy_clickhouse_otlp_url" {
  description = "Host-side OTel Collector OTLP/HTTP base URL the agent ships to (compose maps host 4320 → container 4318). Not account-bearing."
  type        = string
  default     = "http://host.docker.internal:4320"
}

variable "orders_api_image_tag" {
  description = "Tag for the orders-api image (fallback when a digest is not pinned; see orders_api_image_digest)."
  type        = string
  default     = "latest"
}

variable "orders_api_image_digest" {
  description = "orders-api image digest (sha256:...) from `just ci`. When set, orders-api is pinned by digest instead of tag."
  type        = string
  default     = ""
}
