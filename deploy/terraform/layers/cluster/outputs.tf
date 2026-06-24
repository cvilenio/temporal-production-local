output "orders_namespace" {
  description = "Kubernetes namespace for the orders workload."
  value       = kubernetes_namespace.orders.metadata[0].name
}

output "argocd_namespace" {
  description = "Kubernetes namespace ArgoCD runs in. UI is on host :8090 (NodePort), framed in the demo console at http://localhost:8088 via viz-proxy (ADR-0014)."
  value       = helm_release.argocd.namespace
}

output "temporal_address" {
  description = "Regional Cloud gRPC endpoint the workers connect to (from the cloud layer; API-key auth)."
  value       = local.temporal_address
}

output "cloud_apikey_secret" {
  description = "Name of the k8s Secret holding the Cloud worker API key."
  value       = kubernetes_secret.orders_cloud_apikey.metadata[0].name
}
