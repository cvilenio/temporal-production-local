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

# ── Temporal backend toggle (ADR-0003 / -0005) ───────────────────────────────
# Which Temporal server the workers/apps connect to. `cloud` (default) keeps the
# supported path unchanged; `oss` repoints them at the in-cluster temporal-server.
# The guarded `just switch-backend` recipe sets TF_VAR_temporal_backend; do not
# flip it by hand on a live stack (see docs/RUNMODES.md). TLS stays ON in BOTH
# modes — only the credential type differs (Cloud API key ↔ OSS client cert).
variable "temporal_backend" {
  description = "Temporal backend for the cluster workers/apps: 'cloud' (default) or 'oss'."
  type        = string
  default     = "cloud"
  validation {
    condition     = contains(["cloud", "oss"], var.temporal_backend)
    error_message = "temporal_backend must be 'cloud' or 'oss'."
  }
}

# Whether the in-cluster OSS temporal-server Application exists. DECOUPLED from
# temporal_backend on purpose (confirmed design): switching workers to Cloud must
# NOT prune the OSS server and destroy its state. Set true on first OSS bring-up;
# `just temporal-server-down` sets it false (removes the app + CNPG cluster).
variable "oss_server_enabled" {
  description = "Create the in-cluster OSS temporal-server ArgoCD Application (independent of temporal_backend)."
  type        = bool
  default     = false
}

variable "temporal_server_chart_version" {
  description = "Published version of the temporal-server wrapper OCI chart (matches Chart.yaml / just chart-publish)."
  type        = string
  default     = "0.1.5"
}

variable "temporal_k8s_namespace" {
  description = "Kubernetes namespace the OSS temporal-server + its Postgres run in."
  type        = string
  default     = "temporal"
}

variable "oss_namespace" {
  description = "Temporal namespace on the OSS backend (matches the bootstrap Job; the bare domain name, no account suffix)."
  type        = string
  default     = "ziggymart"
}

variable "oss_temporal_address" {
  description = "In-cluster gRPC address of the OSS frontend the workers/apps dial (mTLS)."
  type        = string
  default     = "temporal-frontend.temporal.svc.cluster.local:7233"
}

# Client-cert Secret names the temporal-server chart issues into the orders
# namespace (must match deploy/charts/temporal-server/values.yaml mtls.clientCertSecrets).
variable "oss_worker_mtls_secret" {
  description = "k8s Secret (in the orders namespace) holding the worker mTLS client cert on the OSS backend."
  type        = string
  default     = "temporal-worker-mtls"
}

variable "oss_client_mtls_secret" {
  description = "k8s Secret holding the orders-api mTLS client cert on the OSS backend."
  type        = string
  default     = "temporal-client-mtls"
}

variable "oss_autoscaler_mtls_secret" {
  description = "k8s Secret holding the autoscaler mTLS client cert on the OSS backend."
  type        = string
  default     = "temporal-autoscaler-mtls"
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
  default     = "0.1.19"
}

variable "autoscaler_chart_version" {
  description = "Published version of the temporal-worker-autoscaler OCI chart (matches Chart.yaml / just chart-publish)."
  type        = string
  default     = "0.1.4"
}

variable "autoscaler_image_tag" {
  description = "Tag for the temporal-worker-autoscaler image (fallback when a digest is not pinned; see autoscaler_image_digest)."
  type        = string
  default     = "latest"
}

variable "autoscaler_image_digest" {
  description = "temporal-worker-autoscaler image digest (sha256:...) from `just ci`. When set, pins the controller by digest instead of tag."
  type        = string
  default     = ""
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
  default     = "0.1.5"
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
