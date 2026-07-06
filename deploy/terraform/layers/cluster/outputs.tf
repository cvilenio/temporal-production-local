output "orders_namespace" {
  description = "Kubernetes namespace for the orders workload."
  value       = kubernetes_namespace.orders.metadata[0].name
}

output "argocd_namespace" {
  description = "Kubernetes namespace ArgoCD runs in. UI is on host :8090 (NodePort), framed in the demo console at http://localhost:8088 via viz-proxy (ADR-0014)."
  value       = helm_release.argocd.namespace
}

output "temporal_backend" {
  description = "Active Temporal backend for the cluster workers/apps ('cloud' or 'oss')."
  value       = var.temporal_backend
}

output "oss_server_enabled" {
  description = "Whether the in-cluster OSS temporal-server Application is deployed (decoupled from temporal_backend)."
  value       = var.oss_server_enabled
}

output "temporal_address" {
  description = "gRPC endpoint the workers connect to — regional Cloud endpoint (API-key auth) or the in-cluster OSS frontend (mTLS)."
  value       = local.temporal_address
}

output "cloud_apikey_secret" {
  description = "Name of the k8s Secret holding the Cloud worker API key (empty on the OSS backend)."
  value       = one(kubernetes_secret.orders_cloud_apikey[*].metadata[0].name)
}
